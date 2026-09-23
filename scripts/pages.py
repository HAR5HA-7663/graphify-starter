"""Frontmatter helpers shared by the Jev-backed features."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from . import config

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


def parse_frontmatter(md: str) -> tuple[dict, str]:
    """Return (fields, body). Handles `key: value`, `tags: [a, b]` and `- item` lists."""
    m = _FM_RE.match(md)
    if not m:
        return {}, md
    fields: dict = {}
    current: str | None = None
    for line in m.group(1).splitlines():
        item = re.match(r"^\s*-\s+(.*)$", line)
        if item and current:
            fields.setdefault(current, [])
            if isinstance(fields[current], list):
                fields[current].append(item.group(1).strip().strip("'\""))
            continue
        kv = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not kv:
            continue
        current, value = kv.group(1), kv.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            fields[current] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        elif value == "":
            fields[current] = []
        else:
            fields[current] = value.strip("'\"")
    return fields, md[m.end():]


@lru_cache(maxsize=512)
def is_private(source_rel: str | None) -> bool:
    """True when the file behind a chunk is tagged private — its text must not leave for Jev."""
    if not source_rel:
        return False
    path = config.BRAIN_ROOT / source_rel
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return False
    tags = parse_frontmatter(head)[0].get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    return config.PRIVATE_TAG in [str(t).lower() for t in tags]


def excerpt(text: str, limit: int = 220) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def wiki_pages() -> list[Path]:
    return sorted(config.WIKI_PAGES.glob("*.md"))
