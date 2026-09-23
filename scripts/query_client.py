"""Client side of the query daemon — must stay free of heavy imports (no chromadb)."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys

from . import config

# Env vars that change query behaviour. A daemon started under different values (or
# older code, or an older .env) must not answer — see fingerprint().
_BEHAVIOUR_ENV = ("BRAIN_PRIVACY_STRICT", "BRAIN_JEV", "BRAIN_CONFIDENCE_THRESHOLD")
_CONNECT_TIMEOUT_S = 0.2
# Longest a daemon answer can legitimately take: embedding (10 s cap) + Jev deadline.
_ANSWER_TIMEOUT_S = 15.0


def fingerprint() -> str:
    parts = [f"{name}={os.environ.get(name, '')}" for name in _BEHAVIOUR_ENV]
    watched = sorted(config.SCRIPTS_DIR.glob("*.py")) + [config.BRAIN_ROOT / ".env"]
    for path in watched:
        try:
            parts.append(f"{path.name}:{path.stat().st_mtime_ns}")
        except OSError:
            parts.append(f"{path.name}:-")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def ask(request: dict) -> dict | None:
    """The daemon's payload, or None when there is no usable daemon (caller runs in-process)."""
    if not config.QUERY_DAEMON_ENABLED or not config.QUERY_SOCKET.exists():
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_CONNECT_TIMEOUT_S)
            sock.connect(str(config.QUERY_SOCKET))
            sock.settimeout(_ANSWER_TIMEOUT_S)
            sock.sendall(json.dumps({"fingerprint": fingerprint(), "request": request}).encode() + b"\n")
            reply = sock.makefile("rb").readline()
        answer = json.loads(reply)
    except (OSError, ValueError):
        return None
    return answer.get("payload")  # absent on "restart" / "error" replies


def spawn() -> None:
    """Start a daemon in the background; a no-op if one already holds the lock."""
    if not config.QUERY_DAEMON_ENABLED:
        return
    try:
        with open(config.QUERY_DAEMON_LOG, "ab") as log:
            subprocess.Popen(
                [sys.executable, "-m", "scripts.query_daemon"],
                cwd=str(config.BRAIN_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                start_new_session=True,
            )
    except OSError:
        pass  # the daemon is an optimisation; the query already succeeded
