"""Semantic query over brain_wiki with brain_raw fallback.

Latency notes (2026-09-21): the embedding request, the chromadb import, the HNSW
index load and the tokenizer load used to run back to back. They are independent,
so the network call and the tokenizer load now run in threads while the main thread
imports chromadb and warms the index. The optional Jev rerank only fires when the
cosine score is ambiguous and is capped by config.JEV_QUERY_TIMEOUT_S.

2026-09-23: the ~1.5 s floor left after that was `import chromadb` plus the index load,
paid by every process. The CLI now asks a warm daemon first (query_daemon.py, started
on demand, exits when idle) and only runs in-process when none is up.
"""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import click

from . import config, fastembed, jev, query_client, rerank


_LEADING_HEADINGS_RE = re.compile(r"\A(?:[ \t]*#{1,6} .*\n+)+")
_LINK_RE = re.compile(r"\[\[[^\]]*\]\]")
_WORD_RE = re.compile(r"[A-Za-z0-9]{2,}")


def _link_only(text: str) -> bool:
    """True for chunks that are just headings + [[wiki links]] — topical but contentless.

    Only the heading block the chunker prepends is stripped: a `#` line further down is
    body text (a shell comment inside a code fence, for instance) and counts as content.
    """
    if "```" in text:
        return False
    body = _LEADING_HEADINGS_RE.sub("", text + "\n")
    return len(_WORD_RE.findall(_LINK_RE.sub(" ", body))) <= config.LINK_ONLY_MAX_WORDS


def _query_collection(store, collection: str, query_embedding: list[float], k: int) -> list[dict]:
    coll = store.get_collection(collection)
    res = coll.query(
        query_embeddings=[query_embedding],
        n_results=k + config.QUERY_OVERFETCH,
        include=["documents", "metadatas", "distances"],
    )
    results: list[dict] = []
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        meta = meta or {}  # chunks written without metadata come back as None
        if _link_only(doc or ""):
            continue
        results.append(
            {
                "score": round(1.0 - float(dist), 4),
                "layer": collection,
                "source_rel": meta.get("source_rel"),
                "heading_path": meta.get("heading_path"),
                "chunk_idx": meta.get("chunk_idx"),
                "embedding_model": meta.get("embedding_model"),
                "text": doc or "",
            }
        )
    return results[:k]


def _warm_index(store, collection: str) -> None:
    """Force the HNSW segment to load now, while the embedding request is still in flight."""
    try:
        coll = store.get_collection(collection)
        seed = coll.get(limit=1, include=["embeddings"])
        vectors = seed.get("embeddings")
        if vectors is not None and len(vectors):
            coll.query(query_embeddings=[list(vectors[0])], n_results=1, include=[])
    except Exception:  # noqa: BLE001 — warming is an optimisation, never a failure
        pass


def _load_chunker():
    from . import chunker  # loads the tiktoken encoding (~0.3 s)

    return chunker


def _trim_to_budget(chunker, results: list[dict], budget: int) -> list[dict]:
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
@click.option(
    "--rerank",
    "rerank_mode",
    default="auto",
    type=click.Choice(["auto", "always", "off"]),
    help="auto: ask Jev only when the cosine score is ambiguous. always/off for testing.",
)
def main(question: str, collection: str, k: int, threshold: float | None, budget: int, rerank_mode: str) -> None:
    config.validate_or_exit()
    request = {
        "question": question,
        "collection": collection,
        "k": k,
        "threshold": threshold,
        "budget": budget,
        "rerank_mode": rerank_mode,
    }
    # A warm query daemon (scripts/query_daemon.py) already has chromadb imported and the
    # index loaded, so it answers in roughly the embedding round-trip. Without one, answer
    # here as before and start one in the background for the next query.
    payload = query_client.ask(request)
    if payload is None:
        payload = run(**request)
        payload["served_by"] = "process"
        query_client.spawn()
    click.echo(json.dumps(payload, indent=2))


def run(question: str, collection: str, k: int, threshold: float | None, budget: int, rerank_mode: str) -> dict:
    """One query -> the JSON payload. Shared by the CLI and the query daemon."""
    threshold = threshold if threshold is not None else config.resolve_threshold()
    started = time.perf_counter()
    use_jev = rerank_mode != "off" and collection == "auto" and jev.enabled()

    pool = ThreadPoolExecutor(max_workers=2)
    embedding_job = pool.submit(fastembed.embed_query, question)
    chunker_job = pool.submit(_load_chunker)
    if use_jev:
        threading.Thread(target=jev.warm, daemon=True).start()  # TLS handshake off the critical path

    from . import store  # chromadb import (~0.8 s) overlaps the embedding request

    _warm_index(store, config.COLL_RAW if collection == config.COLL_RAW else config.COLL_WIKI)
    embedding = embedding_job.result()
    ready = time.perf_counter()

    wiki_results: list[dict] = []
    raw_results: list[dict] = []
    layer = collection

    if collection in ("auto", config.COLL_WIKI):
        wiki_results = _query_collection(store, config.COLL_WIKI, embedding, k)

    if collection == "auto":
        wiki_top = wiki_results[0]["score"] if wiki_results else -1.0
        if wiki_top >= threshold:
            layer = config.COLL_WIKI
            results = wiki_results
        else:
            # wiki missed the preference gate — consult raw, but keep whichever
            # layer is actually closer so a borderline wiki hit (e.g. 0.42) is not
            # discarded in favour of a worse raw one.
            raw_results = _query_collection(store, config.COLL_RAW, embedding, k)
            raw_top = raw_results[0]["score"] if raw_results else -1.0
            if raw_top >= wiki_top:
                layer = config.COLL_RAW
                results = raw_results
            else:
                layer = config.COLL_WIKI
                results = wiki_results
    elif collection == config.COLL_WIKI:
        results = wiki_results
    else:
        raw_results = _query_collection(store, config.COLL_RAW, embedding, k)
        results = raw_results

    top_score = results[0]["score"] if results else 0.0

    # Authoritative in-brain verdict. Cosine decides unless the score is ambiguous and
    # Jev answered in time, in which case Jev's per-passage probabilities decide.
    # Consumers (the brain-query skill) MUST trust `verdict`/`in_brain` and never
    # re-threshold with a hardcoded number.
    floor = config.TRAINING_FALLBACK_FLOOR
    in_brain = top_score >= floor
    confidence = "high" if (top_score >= config.RERANK_BAND[1] or top_score < config.RERANK_BAND[0]) else "unchecked"
    verdict_source = "cosine"
    rerank_info: dict = {"mode": rerank_mode, "called": False}

    if use_jev and results and (rerank_mode == "always" or rerank.in_band(top_score)):
        if not raw_results:
            raw_results = _query_collection(store, config.COLL_RAW, embedding, k)
        jev_started = time.perf_counter()
        rerank_info["called"] = True
        try:
            in_brain, confidence, results, stats = rerank.run(question, wiki_results, raw_results, threshold)
            verdict_source = "jev"
            layer = results[0]["layer"] if results else layer
            top_score = results[0]["score"] if results else top_score
            rerank_info.update(stats)
        except jev.JevError as e:
            rerank_info["error"] = str(e)  # cosine result stands
        rerank_info["ms"] = int((time.perf_counter() - jev_started) * 1000)
    elif not use_jev and rerank_mode != "off":
        rerank_info["skipped"] = "privacy_strict" if config.PRIVACY_STRICT else "unavailable"
    else:
        rerank_info["skipped"] = "cosine score decisive" if rerank_mode == "auto" else rerank_mode

    results = _trim_to_budget(chunker_job.result(), results, budget)
    pool.shutdown(wait=False)
    verdict = layer if in_brain else "not_in_brain"

    payload = {
        "question": question,
        "layer": layer,
        "verdict": verdict,
        "in_brain": in_brain,
        "verdict_source": verdict_source,
        "confidence": confidence,
        "provider": config.EMBED_PROVIDER,
        "threshold": threshold,
        "fallback_floor": floor,
        "top_score": top_score,
        "budget_tokens": budget,
        "rerank": rerank_info,
        "timings_ms": {
            "startup_and_embedding": int((ready - started) * 1000),
            "total": int((time.perf_counter() - started) * 1000),
        },
        "results": results,
    }
    if verdict_source == "jev" and results and "p_answers" in results[0]:
        payload["top_p"] = results[0]["p_answers"]
    if collection == "auto" and layer == config.COLL_RAW:
        payload["wiki_top_if_fallback"] = wiki_results[:3]
    return payload


if __name__ == "__main__":
    main()
