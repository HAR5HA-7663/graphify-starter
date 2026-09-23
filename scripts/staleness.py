"""Staleness report for wiki pages — review interval scaled to how fast a page's facts move.

    python -m scripts.staleness            # pages overdue for review, most overdue first
    python -m scripts.staleness --all      # every page
    python -m scripts.staleness --refresh  # ignore the cache and re-score everything
    python -m scripts.staleness --only project-alpha,team --all

A flat "older than 90 days" rule flags evergreen architecture notes while missing a
loan-rate page that went stale in three weeks. Jev rates each page's volatility 0..3
and the review interval is interpolated from config.STALENESS_REVIEW_DAYS. Scores are
cached by content hash, so a lint run only calls Jev for pages that changed. Private
pages, privacy-strict mode, and Jev failures all fall back to the flat rule.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import click

from . import config, jev, pages

LEVELS = [
    "Evergreen: concepts, architecture or history that stays true for years",
    "Slow: stable facts that are worth re-checking about once a year",
    "Medium: project state, plans or configurations that shift over a few months",
    "Fast: live status, balances, rates, deadlines or open incidents that change within weeks",
]
QUESTION = "How quickly does the information on this page go out of date?"
STATE_CHARS = 6000


def review_days(volatility: float) -> int:
    """Interpolate the review interval for a 0..3 volatility score."""
    table = config.STALENESS_REVIEW_DAYS
    v = min(max(volatility, 0.0), len(table) - 1.0)
    lo = int(v)
    hi = min(lo + 1, len(table) - 1)
    return round(table[lo] + (table[hi] - table[lo]) * (v - lo))


def _parse_date(value) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _load_cache() -> dict:
    try:
        return json.loads(config.STALENESS_CACHE.read_text())
    except (OSError, ValueError):
        return {}


def _score(path: Path, fields: dict, body: str) -> dict:
    state = f"TITLE: {fields.get('title', path.stem)}\nTAGS: {fields.get('tags', [])}\n\n{body[:STATE_CHARS]}"
    answers = jev.ask(state, {"volatility": {"type": "score", "instructions": QUESTION, "criteria": LEVELS}},
                      purpose="staleness")
    a = answers.get("volatility") or {}
    if not isinstance(a.get("score"), (int, float)):
        raise jev.JevError("no volatility score")
    return {"volatility": round(float(a["score"]), 2), "confidence": round(float(a.get("confidence", 0)), 2)}


def report(refresh: bool = False, today: date | None = None, only: set[str] | None = None) -> dict:
    today = today or date.today()
    cache = {} if refresh else _load_cache()
    use_jev = jev.enabled()
    entries, to_score = [], []

    for path in pages.wiki_pages():
        if only and path.stem not in only:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        fields, body = pages.parse_frontmatter(text)
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        entry = {"slug": path.stem, "last_updated": str(fields.get("last_updated", "")), "_hash": digest,
                 "_path": path, "_fields": fields, "_body": body}
        tags = [str(t).lower() for t in (fields.get("tags") or [])] if isinstance(fields.get("tags"), list) else []
        if fields.get("deprecated") in ("true", True):
            continue
        cached = cache.get(path.stem)
        if cached and cached.get("hash") == digest:
            entry.update(volatility=cached["volatility"], confidence=cached["confidence"], basis="jev (cached)")
        elif use_jev and config.PRIVATE_TAG not in tags:
            to_score.append(entry)
        entries.append(entry)

    failures = 0
    if to_score:
        with ThreadPoolExecutor(max_workers=config.JEV_WORKERS) as pool:
            jobs = [pool.submit(_score, e["_path"], e["_fields"], e["_body"]) for e in to_score]
            for e, job in zip(to_score, jobs):
                try:
                    e.update(job.result(), basis="jev")
                    cache[e["slug"]] = {"hash": e["_hash"], "volatility": e["volatility"], "confidence": e["confidence"]}
                except jev.JevError:
                    failures += 1
        try:
            config.STALENESS_CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True))
        except OSError:
            pass

    out = []
    for e in entries:
        updated = _parse_date(e["last_updated"])
        if "volatility" in e:
            interval = review_days(e["volatility"])
        else:
            interval, e["basis"] = config.STALENESS_FLAT_DAYS, "flat rule"
        age = (today - updated).days if updated else None
        out.append({
            "slug": e["slug"], "last_updated": e["last_updated"] or None, "age_days": age,
            "volatility": e.get("volatility"), "confidence": e.get("confidence"),
            "review_every_days": interval, "basis": e["basis"],
            "overdue_ratio": round(age / interval, 2) if age is not None else None,
            "stale": age is None or age >= interval,
        })
    out.sort(key=lambda r: -(r["overdue_ratio"] if r["overdue_ratio"] is not None else 99))
    return {"date": today.isoformat(), "pages": len(out), "stale": sum(r["stale"] for r in out),
            "scored_now": len(to_score) - failures, "jev_failures": failures,
            "mode": "jev" if use_jev else ("no Jev calls (privacy_strict): cached scores, else flat rule" if config.PRIVACY_STRICT
                                           else "no Jev calls (jev off): cached scores, else flat rule"),
            "results": out}


@click.command()
@click.option("--all", "show_all", is_flag=True, help="List every page, not only the stale ones.")
@click.option("--refresh", is_flag=True, help="Ignore the cache and re-score every page.")
@click.option("--only", default="", help="Comma-separated slugs to limit the run to.")
def main(show_all: bool, refresh: bool, only: str) -> None:
    # No validate_or_exit(): this tool never embeds, so privacy-strict mode must not stop it —
    # it just runs on the flat rule (jev.enabled() is False under strict).
    rep = report(refresh=refresh, only={s.strip() for s in only.split(",") if s.strip()} or None)
    if not show_all:
        rep["results"] = [r for r in rep["results"] if r["stale"]]
    click.echo(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
