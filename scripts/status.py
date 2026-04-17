"""Report brain vector store health + filesystem sync + watcher status."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import click

from . import config, store


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collection_stats(name: str) -> dict:
    coll = store.get_collection(name)
    count = coll.count()
    got = coll.get(include=["metadatas"]) if count else {"metadatas": []}
    metas = got.get("metadatas") or []
    sources: dict[str, dict] = {}
    models: set[str] = set()
    for m in metas:
        sp = m.get("source_path")
        if not sp:
            continue
        sources.setdefault(sp, {"hash": m.get("source_hash"), "model": m.get("embedding_model"), "mtime": m.get("source_mtime")})
        if m.get("embedding_model"):
            models.add(m["embedding_model"])
    stale: list[str] = []
    hash_mismatch: list[str] = []
    for sp, info in sources.items():
        p = Path(sp)
        if not p.exists():
            stale.append(sp)
            continue
        if info.get("hash") and _sha256(p) != info["hash"]:
            hash_mismatch.append(sp)
    return {
        "chunks": count,
        "files": len(sources),
        "embedding_models": sorted(models),
        "latest_mtime": max((s.get("mtime") or 0 for s in sources.values()), default=0),
        "stale_paths": stale,
        "hash_mismatch_paths": hash_mismatch,
        "sources": set(sources.keys()),
    }


def _fs_markdowns() -> dict:
    raw_files = sorted(p for p in config.RAW_DIR.glob("*.md") if not p.name.startswith("."))
    wiki_pages = sorted(p for p in config.WIKI_PAGES.glob("*.md") if not p.name.startswith("."))
    wiki_top = [p for p in (config.WIKI_TOP / "index.md", config.WIKI_TOP / "overview.md") if p.exists()]
    return {"raw": raw_files, "wiki_pages": wiki_pages, "wiki_top": wiki_top}


def _watcher_health() -> dict:
    pid = None
    exit_status = None
    try:
        out = subprocess.run(
            ["launchctl", "list", "com.harsha.brain-watch"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                line = line.strip().rstrip(";").strip()
                if line.startswith('"PID"') and "=" in line:
                    pid = line.split("=", 1)[1].strip()
                elif line.startswith('"LastExitStatus"') and "=" in line:
                    exit_status = line.split("=", 1)[1].strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    log_mtime = None
    if config.LOG_PATH.exists():
        log_mtime = int(config.LOG_PATH.stat().st_mtime)
    return {"pid": pid, "last_exit_status": exit_status, "log_mtime": log_mtime}


def render_text(report: dict) -> str:
    lines: list[str] = []
    lines.append("# brain status\n")
    lines.append(f"Provider:        {report['provider']}")
    lines.append(f"Embed model:     {report['embed_model']}")
    lines.append(f"Privacy strict:  {'on' if report['privacy_strict'] else 'off'}")
    lines.append("")

    for name in (config.COLL_WIKI, config.COLL_RAW):
        s = report["collections"][name]
        lines.append(f"[{name}]")
        lines.append(f"  files:        {s['files']}")
        lines.append(f"  chunks:       {s['chunks']}")
        lines.append(f"  models:       {', '.join(s['embedding_models']) or '—'}")
        if len(s["embedding_models"]) > 1:
            lines.append("  ⚠  mixed embedding models — dim mismatch risk. Run reembed_all.py --wipe.")
        if s["stale_paths"]:
            lines.append(f"  stale (on-disk gone): {len(s['stale_paths'])}")
        if s["hash_mismatch_paths"]:
            lines.append(f"  hash drift:   {len(s['hash_mismatch_paths'])}")
        lines.append("")

    fs = report["filesystem"]
    lines.append(f"Raw files:       {fs['raw_total']}  (embedded {fs['raw_embedded']}, pending {fs['raw_pending']})")
    lines.append(f"Wiki pages:      {fs['wiki_total']}  (embedded {fs['wiki_embedded']}, pending {fs['wiki_pending']})")
    if fs["unembedded"]:
        lines.append("Pending embed:")
        for p in fs["unembedded"][:20]:
            lines.append(f"  - {p}")
        if len(fs["unembedded"]) > 20:
            lines.append(f"  … +{len(fs['unembedded']) - 20} more")
    lines.append("")

    w = report["watcher"]
    lines.append("[watcher: com.harsha.brain-watch]")
    if w["pid"]:
        lines.append(f"  pid:          {w['pid']}   exit={w['last_exit_status'] or 'ok'}")
    else:
        lines.append("  pid:          NOT RUNNING")
        lines.append("  → launchctl unload ~/Library/LaunchAgents/com.harsha.brain-watch.plist && launchctl load ~/Library/LaunchAgents/com.harsha.brain-watch.plist")
    if w["log_mtime"]:
        age_s = int(datetime.now(tz=timezone.utc).timestamp()) - w["log_mtime"]
        lines.append(f"  last log:     {age_s}s ago")
    lines.append("")

    # privacy vs provider conflict
    if report["privacy_strict"] and report["embed_model"].startswith("text-embedding-"):
        lines.append("⚠  FATAL: PRIVACY_STRICT=on but EMBED_MODEL is an OpenAI model. Fix scripts/config.py.")
        lines.append("")

    lines.append("Recovery: run scripts/reembed_all.py [--wipe] to rebuild both collections from source.")
    return "\n".join(lines)


@click.command()
@click.option("--json", "as_json", is_flag=True)
def main(as_json: bool) -> None:
    # Skip hard validate so we can still report a misconfiguration
    stats_wiki = _collection_stats(config.COLL_WIKI)
    stats_raw = _collection_stats(config.COLL_RAW)

    fs = _fs_markdowns()
    wiki_all = fs["wiki_pages"] + fs["wiki_top"]
    raw_embedded = {Path(s) for s in stats_raw["sources"]}
    wiki_embedded = {Path(s) for s in stats_wiki["sources"]}
    unembedded_raw = [str(p) for p in fs["raw"] if p.resolve() not in {q.resolve() for q in raw_embedded}]
    unembedded_wiki = [str(p) for p in wiki_all if p.resolve() not in {q.resolve() for q in wiki_embedded}]

    def drop_sources(d: dict) -> dict:
        d.pop("sources", None)
        return d

    report = {
        "provider": config.EMBED_PROVIDER,
        "embed_model": config.EMBED_MODEL,
        "privacy_strict": config.PRIVACY_STRICT,
        "collections": {
            config.COLL_WIKI: drop_sources(stats_wiki),
            config.COLL_RAW: drop_sources(stats_raw),
        },
        "filesystem": {
            "raw_total": len(fs["raw"]),
            "raw_embedded": len(fs["raw"]) - len(unembedded_raw),
            "raw_pending": len(unembedded_raw),
            "wiki_total": len(wiki_all),
            "wiki_embedded": len(wiki_all) - len(unembedded_wiki),
            "wiki_pending": len(unembedded_wiki),
            "unembedded": unembedded_raw + unembedded_wiki,
        },
        "watcher": _watcher_health(),
    }

    if as_json:
        click.echo(json.dumps(report, indent=2, default=str))
    else:
        click.echo(render_text(report))


if __name__ == "__main__":
    main()
