"""Single-query embedding over plain httpx.

The query path embeds exactly one string, so it skips the OpenAI SDK (≈0.5 s to
import) and posts directly. Any failure falls back to embedder.embed_batch, which
keeps the SDK's retry/backoff behaviour. Bulk embedding still goes through embedder.
"""

from __future__ import annotations

import httpx

from . import config

_URL = "https://api.openai.com/v1/embeddings"


def embed_query(text: str) -> list[float]:
    if config.EMBED_PROVIDER == "openai":
        try:
            resp = httpx.post(
                _URL,
                headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
                json={"model": config.EMBED_MODEL, "input": [text]},
                timeout=httpx.Timeout(10.0, connect=3.0),
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            pass
    from . import embedder  # deferred: pulls in the OpenAI SDK

    return embedder.embed_batch([text])[0]
