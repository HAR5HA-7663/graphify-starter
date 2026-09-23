"""Jev rerank for brain queries — replaces the cosine guess only where cosine is ambiguous.

Cosine similarity from text-embedding-3-small puts unrelated chunks at ~0.40–0.50 and
real hits at 0.52–0.76, so a fixed threshold misjudges anything in between. Inside
config.RERANK_BAND we ask Jev, per passage, whether it actually answers the question
and use those calibrated probabilities for the verdict and the ordering.
"""

from __future__ import annotations

from . import config, jev, pages

QUESTION = (
    "Does PASSAGE {n} contain information that directly helps answer the QUESTION? "
    "Answer no if it is only about a related topic."
)


def in_band(best_cosine: float) -> bool:
    lo, hi = config.RERANK_BAND
    return lo <= best_cosine < hi


def build_request(question: str, candidates: list[dict]) -> tuple[str, dict]:
    parts = [f"QUESTION: {question}"]
    questions = {}
    for n, c in enumerate(candidates, start=1):
        where = " > ".join(filter(None, [c.get("source_rel"), c.get("heading_path")]))
        parts.append(f"PASSAGE {n} ({where}):\n{c['text']}")
        questions[f"p{n}"] = {"type": "noul", "instructions": QUESTION.format(n=n)}
    return "\n\n".join(parts), questions


def apply(candidates: list[dict], private: list[dict], answers: dict, gate: float) -> tuple[bool, str, list[dict]]:
    """Merge Jev's probabilities into the candidates -> (in_brain, confidence, ordered results).

    Jev can rescue any result set, but can only reject one whose best cosine score is
    below `gate` — see config.RERANK_BAND. `private` chunks were never sent to Jev; they
    keep the old cosine rule so tagging a page private cannot make it unfindable.
    """
    judged = []
    for n, c in enumerate(candidates, start=1):
        p = jev.noul(answers, f"p{n}")
        if p is None:
            raise jev.JevError(f"no answer for passage {n}")
        judged.append({**c, "p_answers": round(p, 3)})
    judged.sort(key=lambda c: (-c["p_answers"], -c["score"]))

    keep_private = [c for c in private if c["score"] >= config.TRAINING_FALLBACK_FLOOR]
    by_cosine = sorted(judged + private, key=lambda c: -c["score"])
    best_cosine = by_cosine[0]["score"] if by_cosine else 0.0
    needed = config.RERANK_IN_BRAIN_P if best_cosine >= config.TRAINING_FALLBACK_FLOOR else config.RERANK_RESCUE_P
    if judged and judged[0]["p_answers"] >= needed:
        kept = [c for c in judged if c["p_answers"] >= config.RERANK_DROP_P]
        # Jev never saw the private chunks, so cosine is all we have for them: a private chunk
        # that cosine already rates a decent match leads, the rest trail. (Appending them all
        # last let the token budget cut a private page that was the best match.)
        lead = [c for c in keep_private if c["score"] >= gate]
        trail = [c for c in keep_private if c["score"] < gate]
        return True, "high", lead + kept + trail
    if keep_private:
        return True, "low", by_cosine
    if by_cosine and by_cosine[0]["score"] >= gate:
        # No single passage answers it, but cosine rates the match decent and the answer may
        # be spread across the pages. Stay in-brain, keep cosine order, tell the caller.
        return True, "low", by_cosine
    # Weak on cosine AND nothing answers: not in the brain. Keep a few so the caller sees what was rejected.
    return False, "high", by_cosine[:3]


def run(question: str, wiki: list[dict], raw: list[dict], gate: float) -> tuple[bool, str, list[dict], dict]:
    """Judge the top wiki + raw chunks. Raises jev.JevError — caller keeps its cosine result."""
    pool = wiki + raw[: config.RERANK_RAW_CANDIDATES]
    candidates = [c for c in pool if not pages.is_private(c.get("source_rel"))]
    private = [c for c in pool if pages.is_private(c.get("source_rel"))]
    if not candidates:
        raise jev.JevError("every candidate is tagged private")
    state, questions = build_request(question, candidates)
    answers = jev.ask_with_deadline(state, questions, purpose="rerank", deadline=config.JEV_QUERY_TIMEOUT_S)
    in_brain, confidence, results = apply(candidates, private, answers, gate)
    return in_brain, confidence, results, {"judged": len(candidates), "withheld_private": len(private)}
