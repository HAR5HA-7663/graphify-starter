"""Pre-ingest checks: contradictions with existing wiki pages + sensitive content.

Run against a raw source or a drafted page BEFORE writing/updating wiki pages:

    python -m scripts.ingest_check raw/<file>.md
    python -m scripts.ingest_check wiki/pages/<slug>.md      # re-check an existing page

Each chunk of the file is compared with its nearest existing wiki chunks and Jev is
asked (a) whether the two state conflicting facts and (b) whether the new chunk holds
financial-account detail, third-party private data, or credentials. Output is a JSON
list of flags for the ingesting model to act on. Advisory only: exit code is always 0,
and under BRAIN_PRIVACY_STRICT (or with Jev off) it reports `skipped` and the ingest
proceeds on the model's own reading, exactly as before.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click

from . import chunker, config, embedder, jev, pages, store

CONFLICT_Q = (
    "Do NEW PASSAGE and EXISTING PASSAGE {n} state conflicting facts about the same thing "
    "(a different value, date, status, name, owner or decision)? Answer no if they cover "
    "different aspects, or if one only adds detail to the other."
)
SENSITIVE_QS = {
    "financial_detail": (
        "Does NEW PASSAGE contain an individual person's private finances: their bank, card or loan "
        "account numbers, account balances, personal payments, debts, or salary? Business content does "
        "not count: product pricing, plan prices, company revenue, invoices between companies, and "
        "test or QA transactions are not personal finances."
    ),
    "third_party_private_data": (
        "Does NEW PASSAGE contain private data about a person other than the knowledge-base owner: "
        "home address, date of birth, passport / SSN / government ID numbers, or medical details? "
        "Names, job titles and work contact details do not count."
    ),
    "credentials": "Does NEW PASSAGE contain a usable secret: an API key, password, access token or private key?",
}


def _neighbors(embedding: list[float], exclude_source: str) -> list[dict]:
    coll = store.get_collection(config.COLL_WIKI)
    res = coll.query(
        query_embeddings=[embedding],
        n_results=config.INGEST_NEIGHBORS + 4,  # headroom for the filters below
        include=["documents", "metadatas", "distances"],
    )
    out: list[dict] = []
    for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        meta = meta or {}
        src = meta.get("source_rel")
        if src == exclude_source or pages.is_private(src):
            continue
        out.append({"source_rel": src, "heading_path": meta.get("heading_path"), "text": doc or "",
                    "score": round(1.0 - float(dist), 4)})
        if len(out) == config.INGEST_NEIGHBORS:
            break
    return out


def _check_chunk(chunk: chunker.Chunk, neighbors: list[dict]) -> dict:
    parts = [f"NEW PASSAGE:\n{chunk.text}"]
    questions = {name: {"type": "noul", "instructions": q} for name, q in SENSITIVE_QS.items()}
    for n, nb in enumerate(neighbors, start=1):
        parts.append(f"EXISTING PASSAGE {n} ({nb['source_rel']}):\n{nb['text']}")
        questions[f"conflict_{n}"] = {"type": "noul", "instructions": CONFLICT_Q.format(n=n)}
    return jev.ask("\n\n".join(parts), questions, purpose="ingest_check")


def check(path: Path) -> dict:
    source_rel = str(path.resolve().relative_to(config.BRAIN_ROOT)) if config.BRAIN_ROOT in path.resolve().parents else str(path)
    report: dict = {"file": source_rel, "conflicts": [], "sensitive": []}
    if not jev.enabled():
        report["skipped"] = "privacy_strict" if config.PRIVACY_STRICT else "jev disabled"
        return report
    if pages.is_private(source_rel):
        report["skipped"] = f"file is tagged {config.PRIVATE_TAG}"
        return report

    chunks = chunker.chunk_markdown(path)
    if len(chunks) > config.INGEST_MAX_CHUNKS:
        report["truncated_to_chunks"] = config.INGEST_MAX_CHUNKS
        chunks = chunks[: config.INGEST_MAX_CHUNKS]
    if not chunks:
        report["skipped"] = "no content"
        return report

    started = time.perf_counter()
    embeddings = embedder.embed_batch([c.text for c in chunks])
    neighbor_sets = [_neighbors(e, source_rel) for e in embeddings]

    failures = 0
    with ThreadPoolExecutor(max_workers=config.JEV_WORKERS) as pool:
        jobs = [pool.submit(_check_chunk, c, nbs) for c, nbs in zip(chunks, neighbor_sets)]
        for chunk, nbs, job in zip(chunks, neighbor_sets, jobs):
            try:
                answers = job.result()
            except jev.JevError:
                failures += 1
                continue
            heading = " > ".join(chunk.heading_path) or "(top)"
            for n, nb in enumerate(nbs, start=1):
                p = jev.noul(answers, f"conflict_{n}")
                if p is not None and p >= config.INGEST_FLAG_P:
                    report["conflicts"].append({
                        "p": round(p, 2),
                        "new": {"heading": heading, "excerpt": pages.excerpt(chunk.text)},
                        "existing": {"source_rel": nb["source_rel"], "heading": nb["heading_path"],
                                     "excerpt": pages.excerpt(nb["text"])},
                    })
            for kind in SENSITIVE_QS:
                p = jev.noul(answers, kind)
                if p is not None and p >= config.INGEST_FLAG_P:
                    report["sensitive"].append({"kind": kind, "p": round(p, 2), "heading": heading,
                                                "excerpt": pages.excerpt(chunk.text, 120)})

    report["conflicts"].sort(key=lambda c: -c["p"])
    report["sensitive"].sort(key=lambda s: -s["p"])
    report["stats"] = {"chunks_checked": len(chunks), "jev_failures": failures,
                       "seconds": round(time.perf_counter() - started, 2)}
    if failures == len(chunks):
        report["skipped"] = "every Jev call failed — rely on manual reading"
    return report


@click.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def main(file: Path) -> None:
    if jev.enabled():  # when Jev is off (strict mode included) check() reports `skipped` without embedding
        config.validate_or_exit()
    click.echo(json.dumps(check(file), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
