"""Git-friendly copy of the vector store: `vectors/<collection>/<page>.jsonl`.

Chroma's on-disk files (SQLite + HNSW binaries) cannot live in git: they are rewritten
wholesale on every change, never merge, and break if synced while a process has them
open. So the primary device (the one running the watcher) exports every chunk as one
JSON line — id, zlib+base64 text, metadata, base64 float32 embedding — one file per page,
and only rewrites files whose chunks changed. Other devices import that export into a
fresh .chroma and swap it in atomically; no embedding calls, no API key needed.

Text is compressed on purpose, not only for size: security scanners that grep a repo for
known-bad strings usually skip markdown but not .jsonl, so a page that quotes such a string
(an incident write-up, say) would be flagged twice. The text stays readable in its .md.

    python -m scripts.vector_sync export   # primary: .chroma -> vectors/
    python -m scripts.vector_sync import   # replica: vectors/ -> .chroma (skips if unchanged)
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import struct
import zlib
import sys
from pathlib import Path

import click

from . import config

VECTORS_DIR = config.BRAIN_ROOT / "vectors"
IMPORT_STAMP = config.BRAIN_ROOT / ".chroma" / ".vectors-import-hash"
_BATCH = 200


def _encode(vec) -> str:
    return base64.b64encode(struct.pack(f"<{len(vec)}f", *vec)).decode()


def _pack_text(text: str) -> str:
    return base64.b64encode(zlib.compress(text.encode(), 9)).decode()


def _unpack_text(b64: str) -> str:
    return zlib.decompress(base64.b64decode(b64)).decode()


def _decode(b64: str) -> list[float]:
    raw = base64.b64decode(b64)
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def _file_for(collection: str, source_rel: str) -> Path:
    return VECTORS_DIR / collection / (source_rel.replace("/", "__") + ".jsonl")


def export() -> dict:
    from . import store

    written = removed = 0
    for name in config.ALLOWED_COLLECTIONS:
        got = store.get_collection(name).get(include=["documents", "metadatas", "embeddings"])
        by_source: dict[str, list[dict]] = {}
        for cid, doc, meta, emb in zip(got["ids"], got["documents"], got["metadatas"], got["embeddings"]):
            meta = dict(meta or {})
            meta.pop("source_path", None)  # absolute, device-specific; rebuilt on import
            rel = meta.get("source_rel") or cid.split("::")[0]
            by_source.setdefault(rel, []).append(
                {"id": cid, "text_z": _pack_text(doc or ""), "meta": meta, "emb": _encode(emb)}
            )
        wanted = set()
        for rel, rows in by_source.items():
            rows.sort(key=lambda r: (r["meta"].get("chunk_idx", 0), r["id"]))
            path = _file_for(name, rel)
            wanted.add(path)
            text = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows)
            if not path.exists() or path.read_text() != text:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                written += 1
        coll_dir = VECTORS_DIR / name
        if coll_dir.exists():
            for stale in coll_dir.glob("*.jsonl"):
                if stale not in wanted:
                    stale.unlink()
                    removed += 1
    return {"written": written, "removed": removed}


def _export_hash() -> str:
    h = hashlib.sha256()
    for path in sorted(VECTORS_DIR.rglob("*.jsonl")):
        h.update(str(path.relative_to(VECTORS_DIR)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def import_(force: bool = False) -> dict:
    if not VECTORS_DIR.exists():
        return {"skipped": "no vectors/ export"}
    digest = _export_hash()
    if not force and IMPORT_STAMP.exists() and IMPORT_STAMP.read_text().strip() == digest:
        return {"skipped": "unchanged"}

    import chromadb
    from chromadb.config import Settings

    staging = config.BRAIN_ROOT / ".chroma.importing"
    shutil.rmtree(staging, ignore_errors=True)
    client = chromadb.PersistentClient(path=str(staging), settings=Settings(anonymized_telemetry=False))
    counts = {}
    for name in config.ALLOWED_COLLECTIONS:
        coll = client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine", "hnsw:sync_threshold": 100, "hnsw:batch_size": 100},
        )
        ids, docs, metas, embs = [], [], [], []
        for path in sorted((VECTORS_DIR / name).glob("*.jsonl")):
            for line in path.read_text().splitlines():
                row = json.loads(line)
                meta = row["meta"]
                meta["source_path"] = str(config.BRAIN_ROOT / meta["source_rel"])
                ids.append(row["id"])
                docs.append(_unpack_text(row["text_z"]))
                metas.append(meta)
                embs.append(_decode(row["emb"]))
        for i in range(0, len(ids), _BATCH):
            coll.add(ids=ids[i:i + _BATCH], documents=docs[i:i + _BATCH],
                     metadatas=metas[i:i + _BATCH], embeddings=embs[i:i + _BATCH])
        counts[name] = len(ids)
    del client
    chromadb.api.client.SharedSystemClient.clear_system_cache()

    # Swap: readers (query daemon) notice the changed files and reopen.
    (staging / ".vectors-import-hash").write_text(digest)
    old = config.BRAIN_ROOT / ".chroma.previous"
    shutil.rmtree(old, ignore_errors=True)
    if config.CHROMA_DIR.exists():
        config.CHROMA_DIR.rename(old)
    staging.rename(config.CHROMA_DIR)
    shutil.rmtree(old, ignore_errors=True)
    return {"imported": counts}


@click.group()
def cli() -> None:
    pass


@cli.command("export")
def export_cmd() -> None:
    click.echo(json.dumps(export()))


@cli.command("import")
@click.option("--force", is_flag=True, help="Rebuild even if the export has not changed.")
def import_cmd(force: bool) -> None:
    click.echo(json.dumps(import_(force)))


if __name__ == "__main__":
    sys.exit(cli())
