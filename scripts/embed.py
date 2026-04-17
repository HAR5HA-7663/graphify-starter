"""Embed a markdown file into brain_raw or brain_wiki."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import click

from . import chunker, config, embedder, store


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _unchanged(collection: str, source_path: str, source_hash: str, expected_chunks: int) -> bool:
    existing = store.existing_for_source(collection, source_path)
    if not existing:
        return False
    if len(existing) != expected_chunks:
        return False
    return all(m.get("source_hash") == source_hash for m in existing)


def embed_file(path: Path, collection: str, dry_run: bool = False, force: bool = False) -> dict:
    path = path.resolve()
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    if path.suffix.lower() != ".md":
        return {"status": "skipped", "reason": "not-md", "path": str(path)}
    if path.name.startswith("."):
        return {"status": "skipped", "reason": "dotfile", "path": str(path)}

    size = path.stat().st_size
    if size > config.MAX_FILE_BYTES:
        return {
            "status": "skipped",
            "reason": "too-large",
            "size_mb": round(size / 1024 / 1024, 2),
            "limit_mb": config.MAX_FILE_MB,
            "path": str(path),
        }

    content = path.read_bytes()
    source_hash = _sha256(content)

    chunks = chunker.chunk_markdown(path)
    if not chunks:
        return {"status": "skipped", "reason": "empty", "path": str(path)}

    source_rel = str(path.relative_to(config.BRAIN_ROOT))
    source_path_str = str(path)

    if not force and _unchanged(collection, source_path_str, source_hash, len(chunks)):
        return {
            "status": "unchanged",
            "path": str(path),
            "source_rel": source_rel,
            "chunks": len(chunks),
            "collection": collection,
        }

    if dry_run:
        return {
            "status": "dry-run",
            "path": str(path),
            "source_rel": source_rel,
            "chunks": len(chunks),
            "tokens": sum(chunker.count_tokens(c.text) for c in chunks),
            "collection": collection,
        }

    mtime = int(path.stat().st_mtime)
    embeddings = embedder.embed_batch([c.text for c in chunks])

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    for c in chunks:
        ids.append(f"{source_rel}::{c.chunk_idx}")
        documents.append(c.text)
        metadatas.append(
            {
                "source_path": source_path_str,
                "source_rel": source_rel,
                "source_mtime": mtime,
                "source_hash": source_hash,
                "chunk_idx": c.chunk_idx,
                "heading_path": " > ".join(c.heading_path),
                "collection": collection,
                "embedding_model": config.EMBED_MODEL,
            }
        )

    store.delete_by_source(collection, source_path_str)
    store.upsert_chunks(
        collection,
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )

    return {
        "status": "embedded",
        "path": str(path),
        "source_rel": source_rel,
        "chunks": len(chunks),
        "collection": collection,
        "embedding_model": config.EMBED_MODEL,
    }


@click.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--collection",
    required=True,
    type=click.Choice(list(config.ALLOWED_COLLECTIONS)),
)
@click.option("--dry-run", is_flag=True, help="Chunk + count tokens, no API call, no write.")
@click.option("--force", is_flag=True, help="Re-embed even if hash matches.")
def main(file: Path, collection: str, dry_run: bool, force: bool) -> None:
    config.validate_or_exit()
    result = embed_file(file, collection, dry_run=dry_run, force=force)
    click.echo(json.dumps(result))
    if result["status"] == "missing":
        sys.exit(1)


if __name__ == "__main__":
    main()
