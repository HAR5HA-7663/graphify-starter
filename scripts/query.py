"""Semantic query over brain_wiki with brain_raw fallback."""

from __future__ import annotations

import json

import click

from . import chunker, config, embedder, store


def _query_collection(collection: str, query_embedding: list[float], k: int) -> list[dict]:
    coll = store.get_collection(collection)
    res = coll.query(
        query_embeddings=[query_embedding],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )
    results: list[dict] = []
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        results.append(
            {
                "score": round(1.0 - float(dist), 4),
                "source_rel": meta.get("source_rel"),
                "heading_path": meta.get("heading_path"),
                "chunk_idx": meta.get("chunk_idx"),
                "embedding_model": meta.get("embedding_model"),
                "text": doc,
            }
        )
    return results


def _trim_to_budget(results: list[dict], budget: int) -> list[dict]:
    remaining = budget
    trimmed: list[dict] = []
    for r in results:
        tokens = chunker.count_tokens(r["text"])
        if tokens <= remaining:
            trimmed.append(r)
            remaining -= tokens
            continue
        if remaining <= 0:
            break
        # partial: truncate this chunk's text to remaining tokens
        enc = chunker._ENC  # noqa: SLF001
        ids = enc.encode(r["text"])[:remaining]
        r_copy = dict(r)
        r_copy["text"] = enc.decode(ids) + " …[truncated]"
        trimmed.append(r_copy)
        remaining = 0
        break
    return trimmed


@click.command()
@click.argument("question")
@click.option(
    "--collection",
    default="auto",
    type=click.Choice(["auto", config.COLL_WIKI, config.COLL_RAW]),
)
@click.option("--k", default=5, type=int)
@click.option("--threshold", default=None, type=float)
@click.option("--budget", default=config.QUERY_CONTEXT_BUDGET_TOKENS, type=int)
def main(question: str, collection: str, k: int, threshold: float | None, budget: int) -> None:
    config.validate_or_exit()
    threshold = threshold if threshold is not None else config.resolve_threshold()

    embedding = embedder.embed_batch([question])[0]

    wiki_results: list[dict] = []
    raw_results: list[dict] = []
    layer = collection

    if collection in ("auto", config.COLL_WIKI):
        wiki_results = _query_collection(config.COLL_WIKI, embedding, k)

    if collection == "auto":
        top = wiki_results[0]["score"] if wiki_results else -1.0
        if top >= threshold:
            layer = config.COLL_WIKI
            results = wiki_results
        else:
            raw_results = _query_collection(config.COLL_RAW, embedding, k)
            layer = config.COLL_RAW
            results = raw_results
    elif collection == config.COLL_WIKI:
        results = wiki_results
    else:
        raw_results = _query_collection(config.COLL_RAW, embedding, k)
        results = raw_results

    top_score = results[0]["score"] if results else 0.0
    results = _trim_to_budget(results, budget)

    payload = {
        "question": question,
        "layer": layer,
        "provider": config.EMBED_PROVIDER,
        "threshold": threshold,
        "top_score": top_score,
        "budget_tokens": budget,
        "results": results,
    }
    if collection == "auto" and layer == config.COLL_RAW:
        payload["wiki_top_if_fallback"] = wiki_results[:3]

    click.echo(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
