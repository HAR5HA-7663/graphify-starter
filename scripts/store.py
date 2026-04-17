"""ChromaDB client + collection helpers."""

from typing import Any

import chromadb
from chromadb.config import Settings

from . import config

_client: Any = None


def get_client() -> Any:
    global _client
    if _client is None:
        config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=str(config.CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


def get_collection(name: str):
    if name not in config.ALLOWED_COLLECTIONS:
        raise ValueError(f"Unknown collection: {name}")
    return get_client().get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def delete_collection(name: str) -> None:
    try:
        get_client().delete_collection(name)
    except Exception:
        pass


def delete_by_source(name: str, source_path: str) -> None:
    coll = get_collection(name)
    coll.delete(where={"source_path": source_path})


def upsert_chunks(
    name: str,
    *,
    ids: list[str],
    documents: list[str],
    metadatas: list[dict],
    embeddings: list[list[float]],
) -> None:
    coll = get_collection(name)
    coll.add(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)


def existing_for_source(name: str, source_path: str) -> list[dict]:
    """Return metadata list for chunks matching source_path. Empty if none."""
    coll = get_collection(name)
    res = coll.get(where={"source_path": source_path}, include=["metadatas"])
    return res.get("metadatas") or []
