"""One-shot backfill: re-embed every source file into the correct collection."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from . import config, embed as embed_mod, store


def _collect() -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for p in sorted(config.WIKI_PAGES.glob("*.md")):
        if not p.name.startswith("."):
            items.append((p, config.COLL_WIKI))
    for name in ("index.md", "overview.md"):
        p = config.WIKI_TOP / name
        if p.exists():
            items.append((p, config.COLL_WIKI))
    for p in sorted(config.RAW_DIR.glob("*.md")):
        if not p.name.startswith(".") and p.name != "README.md":
            items.append((p, config.COLL_RAW))
        elif p.name == "README.md":
            # README is the raw/ instruction file; also embed so it's searchable
            items.append((p, config.COLL_RAW))
    return items


@click.command()
@click.option("--wipe", is_flag=True, help="Drop both collections before re-embedding.")
@click.option("--force", is_flag=True, help="Ignore hash dedup; re-embed every file.")
def main(wipe: bool, force: bool) -> None:
    config.validate_or_exit()

    if wipe:
        click.echo("Wiping collections…")
        store.delete_collection(config.COLL_RAW)
        store.delete_collection(config.COLL_WIKI)

    items = _collect()
    total = len(items)
    if total == 0:
        click.echo("No files to embed.")
        return

    counts = {"embedded": 0, "unchanged": 0, "skipped": 0, "error": 0}
    for i, (path, collection) in enumerate(items, start=1):
        try:
            r = embed_mod.embed_file(path, collection, force=force)
        except Exception as e:
            click.echo(f"[{i}/{total}] ERROR {path.name}: {e}", err=True)
            counts["error"] += 1
            continue
        status = r.get("status", "error")
        counts[status] = counts.get(status, 0) + 1
        short = path.relative_to(config.BRAIN_ROOT)
        click.echo(f"[{i}/{total}] {status.upper()} {collection} {short} chunks={r.get('chunks','-')}")
    click.echo(f"\nDone. {counts}")
    if counts["error"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
