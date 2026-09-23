"""Thin client for TypeSafe's Jev decision model.

Jev returns typed probabilities instead of text: you send a `state` plus named
questions (noul = yes/no, choice, score) and get calibrated answers back in
roughly 200–400 ms. Everything here is advisory — callers must catch JevError
and fall back to what they did before Jev existed.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import httpx

from . import config


class JevError(Exception):
    """Jev is disabled, unreachable, too slow, or returned something unusable."""


_client: httpx.Client | None = None
_key: str | None = None
_lock = threading.Lock()


def enabled() -> bool:
    return config.JEV_ENABLED and not config.PRIVACY_STRICT


def _api_key() -> str:
    global _key
    if _key:
        return _key
    key = os.environ.get(config.JEV_KEY_NAME, "").strip()  # ~/brain/.env is loaded by config
    if not key:
        raise JevError(f"{config.JEV_KEY_NAME} not set (add it to ~/brain/.env to enable Jev)")
    _key = key
    return key


def _get_client() -> httpx.Client:
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(
                timeout=httpx.Timeout(config.JEV_BATCH_TIMEOUT_S, connect=2.0),
                limits=httpx.Limits(max_connections=config.JEV_WORKERS, max_keepalive_connections=config.JEV_WORKERS),
            )
        return _client


def warm() -> None:
    """Open the TLS connection ahead of time so the first real call skips the handshake.

    Safe to run in a background thread; never raises.
    """
    if not enabled():
        return
    try:
        _api_key()
        _get_client().get(config.JEV_URL, timeout=2.0)  # 4xx is fine — the socket is what we want
    except Exception:  # noqa: BLE001
        pass


def _log(purpose: str, ms: int, outcome: str, usage: dict | None = None) -> None:
    try:
        tokens = (usage or {}).get("input_tokens", "-")
        if config.JEV_LOG_PATH.exists() and config.JEV_LOG_PATH.stat().st_size > 1_000_000:
            keep = config.JEV_LOG_PATH.read_text().splitlines()[-2000:]
            config.JEV_LOG_PATH.write_text("\n".join(keep) + "\n")
        with config.JEV_LOG_PATH.open("a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {purpose} {ms}ms in_tokens={tokens} {outcome}\n")
    except OSError:
        pass


def _recent_outcomes(purpose: str, limit: int) -> list[tuple[float, bool]]:
    """(unix time, ok?) for the last `limit` logged calls of this purpose, oldest first."""
    try:
        lines = config.JEV_LOG_PATH.read_text().splitlines()[-400:]
    except OSError:
        return []
    out = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 5 and parts[1] == purpose:
            try:
                out.append((time.mktime(time.strptime(parts[0], "%Y-%m-%dT%H:%M:%S")), parts[-1] == "ok"))
            except ValueError:
                continue
    return out[-limit:]


def circuit_open(purpose: str) -> bool:
    """True when the last few calls all failed recently — skip Jev instead of making every
    caller wait out the timeout during an outage. Closes itself after the cool-down."""
    recent = _recent_outcomes(purpose, config.JEV_BREAKER_FAILURES)
    if len(recent) < config.JEV_BREAKER_FAILURES or any(ok for _, ok in recent):
        return False
    return time.time() - recent[-1][0] < config.JEV_BREAKER_COOLDOWN_S


def ask_with_deadline(state: str, questions: dict, *, purpose: str, deadline: float) -> dict:
    """ask(), but never blocks the caller longer than `deadline` seconds in total.

    httpx timeouts are per phase (connect, then read), so they can add up past the cap.
    The call runs in a daemon thread that the process does not wait for on exit.
    """
    if circuit_open(purpose):
        raise JevError(f"circuit open: last {config.JEV_BREAKER_FAILURES} {purpose} calls failed")
    box: dict = {}

    def work() -> None:
        try:
            box["answers"] = ask(state, questions, purpose=purpose, timeout=deadline)
        except Exception as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(deadline)
    if "answers" in box:
        return box["answers"]
    if t.is_alive():
        _log(purpose, int(deadline * 1000), "deadline")
        raise JevError(f"no answer within {deadline}s")
    raise box["error"] if isinstance(box.get("error"), JevError) else JevError(str(box.get("error")))


def ask(state: str, questions: dict, *, purpose: str, timeout: float | None = None) -> dict:
    """Return Jev's `answers` dict for the given questions. Raises JevError on any failure.

    Logs one line per call to scripts/jev.log (timing + token count, never content).
    """
    if not enabled():
        raise JevError("privacy_strict" if config.PRIVACY_STRICT else "disabled (BRAIN_JEV=off)")
    started = time.perf_counter()
    outcome, usage = "error", None
    try:
        resp = _get_client().post(
            config.JEV_URL,
            headers={"Authorization": f"Bearer {_api_key()}"},
            json={"model": config.JEV_MODEL, "state": state, "questions": questions},
            timeout=timeout or config.JEV_BATCH_TIMEOUT_S,
        )
        if resp.status_code != 200:
            outcome = f"http_{resp.status_code}"
            raise JevError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        answers = body.get("answers")
        if not isinstance(answers, dict):
            outcome = "bad_body"
            raise JevError("response had no answers")
        outcome, usage = "ok", body.get("usage")
        return answers
    except httpx.TimeoutException as e:
        outcome = "timeout"
        raise JevError(f"timed out after {timeout or config.JEV_BATCH_TIMEOUT_S}s") from e
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        outcome = type(e).__name__
        raise JevError(str(e)) from e
    finally:
        _log(purpose, int((time.perf_counter() - started) * 1000), outcome, usage)


def noul(answers: dict, name: str) -> float | None:
    """P(yes) for a noul question, or None if Jev did not answer it."""
    a = answers.get(name)
    if isinstance(a, dict) and isinstance(a.get("noul"), (int, float)):
        return float(a["noul"])
    return None
