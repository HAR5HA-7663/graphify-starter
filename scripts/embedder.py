"""OpenAI embeddings wrapper with batching + retry."""

import time

from openai import APIConnectionError, OpenAI, RateLimitError

from . import config

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns vectors in the same order."""
    if not texts:
        return []
    client = _get_client()
    out: list[list[float]] = []
    batch_size = 100
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        for attempt in range(5):
            try:
                resp = client.embeddings.create(model=config.EMBED_MODEL, input=batch)
                out.extend([d.embedding for d in resp.data])
                break
            except (RateLimitError, APIConnectionError) as e:
                if attempt == 4:
                    raise
                time.sleep(2**attempt)
    return out
