"""Heading-aware markdown chunker with sliding-window fallback."""

import re
from dataclasses import dataclass, field
from pathlib import Path

import tiktoken

from . import config

_ENC = tiktoken.get_encoding("cl100k_base")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class Section:
    heading_path: list[str] = field(default_factory=list)
    body: str = ""


@dataclass
class Chunk:
    text: str
    heading_path: list[str]
    chunk_idx: int


def count_tokens(text: str) -> int:
    return len(_ENC.encode(text))


def split_by_headings(md: str) -> list[Section]:
    """Split markdown on H1/H2/H3 boundaries, tracking the heading stack."""
    matches = list(_HEADING_RE.finditer(md))
    if not matches:
        return [Section(heading_path=[], body=md.strip())]

    sections: list[Section] = []
    stack: list[tuple[int, str]] = []

    preamble = md[: matches[0].start()].strip()
    if preamble:
        sections.append(Section(heading_path=[], body=preamble))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        heading_path = [t for _, t in stack]

        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[body_start:body_end].strip()
        if body:
            sections.append(Section(heading_path=heading_path[:], body=body))

    return sections


def _prepend_heading(text: str, heading_path: list[str]) -> str:
    if not heading_path:
        return text
    header = "\n".join(
        f"{'#' * min(i + 1, 6)} {h}" for i, h in enumerate(heading_path)
    )
    return f"{header}\n\n{text}"


def _sliding_window(body: str, max_tokens: int, overlap: int) -> list[str]:
    tokens = _ENC.encode(body)
    if len(tokens) <= max_tokens:
        return [body]
    pieces: list[str] = []
    step = max_tokens - overlap
    if step <= 0:
        step = max_tokens
    for start in range(0, len(tokens), step):
        window = tokens[start : start + max_tokens]
        if not window:
            break
        pieces.append(_ENC.decode(window))
        if start + max_tokens >= len(tokens):
            break
    return pieces


def pack_sections(
    sections: list[Section],
    max_tokens: int = config.CHUNK_MAX_TOKENS,
    overlap: int = config.CHUNK_OVERLAP_TOKENS,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    idx = 0
    for s in sections:
        pieces = _sliding_window(s.body, max_tokens, overlap)
        for p in pieces:
            text = _prepend_heading(p, s.heading_path)
            chunks.append(Chunk(text=text, heading_path=s.heading_path[:], chunk_idx=idx))
            idx += 1
    return chunks


def chunk_markdown(path: Path) -> list[Chunk]:
    md = path.read_text(encoding="utf-8", errors="replace")
    sections = split_by_headings(md)
    return pack_sections(sections)
