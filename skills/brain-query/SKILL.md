---
name: brain-query
description: Semantic two-tier retrieval over the personal brain at ~/brain/. Queries brain_wiki (synthesized pages) first, falls back to brain_raw (raw source chunks) if confidence is low, and always reports which layer answered with a confidence score. Use when the user says "/brain-query", "search the brain", "semantic search", or asks a factual question about the user, their clients, projects, or any topic covered by the brain where exact slugs aren't known.
---

# brain-query

Two-tier retrieval + synthesis over `~/brain/`.

## Workflow

1. Run the query script via Bash (must `cd` into brain root for the package import to resolve):
   ```
   cd ~/brain && .venv/bin/python -m scripts.query "<question>" --k 5
   ```
2. Parse the JSON output. Important fields:
   - `verdict` — `"brain_wiki"`, `"brain_raw"`, or `"not_in_brain"`. **Authoritative — trust this; never re-threshold yourself with a hardcoded number.**
   - `in_brain` — bool. `false` ⇒ the brain has no real coverage; answer from training data and label it (see step 7).
   - `layer` — `"brain_wiki"` or `"brain_raw"` — which collection the top result came from.
   - `provider`, `threshold` (wiki-vs-raw gate), `fallback_floor` (in-brain cutoff) — all resolved from `scripts/config.py`, for reporting.
   - `top_score` — cosine similarity of the best result (0–1).
   - `verdict_source` — `"cosine"` or `"jev"`. When the cosine score is ambiguous (inside `config.RERANK_BAND`), the script asks the Jev decision model whether each passage actually answers the question and lets that decide. Still authoritative either way — do not second-guess it. `rerank` and `timings_ms` are diagnostics.
   - `confidence` — `"high"`, `"low"`, or `"unchecked"` (Jev unavailable). **`"low"` means the pages are on-topic but no single passage answers the question**: read the full pages in step 3 before answering, and if they genuinely don't answer it, say so and fall back to step 7's training-data label rather than stretching the sources.
   - `results[]` — best first (by `p_answers`, Jev's probability that the passage answers the question, when present; otherwise by cosine `score`). Each has `layer`, `source_rel`, `heading_path`, `chunk_idx`, `text` (the chunk preview, possibly truncated to the token budget). Passages Jev rated irrelevant are already dropped, so every result is worth reading.
   - `wiki_top_if_fallback` — present only when `layer == "brain_raw"`; the nearest wiki misses, for transparency.

3. For each high-scoring result (top 2–3), Read the cited file (`~/brain/<source_rel>`) to get the full page — the preview is truncated to fit the token budget.

4. Synthesize an answer using only those sources. Cite the wiki slug when `layer == "brain_wiki"` (e.g. `[[acme-integration-flow]]`); cite `raw/<filename>` when `layer == "brain_raw"`.

5. **Always** report which layer answered, verbatim on its own line:
   - `Answered from: brain_wiki (score 0.82)`
   - `Answered from: brain_raw (score 0.71)`

6. If `layer == "brain_raw"`, offer to promote the answer into a wiki page via `/wiki-ingest` or `/wiki-save` so the next query hits the cheaper layer.

7. If `in_brain` is `false` (equivalently `verdict == "not_in_brain"`), both layers effectively missed — fall back to training memory and label the answer `[NOT IN BRAIN — training data]` on its own line. The cutoff is `config.TRAINING_FALLBACK_FLOOR`; the script already applied it, so do not hardcode a threshold here.

8. Append a one-line entry to `~/brain/wiki/log.md`:
   ```
   ## [YYYY-MM-DD] brain-query | <short question>
   Layer: brain_wiki|brain_raw  Score: 0.XX  Sources: <slugs or raw filenames>
   ```

## Rules

- Do not bypass the script and Grep directly. The point is semantic retrieval.
- Do not override the `--threshold` unless the user asks — it's resolved per-provider in config.
- Respect the context budget; the script already trims previews. If the truncation hurts, the Read in step 3 fills the gap.
- If the script exits non-zero, report the error to the user — do not silently fall back to training data.
