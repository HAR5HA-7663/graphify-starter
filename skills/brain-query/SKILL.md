---
name: brain-query
description: Semantic two-tier retrieval over the personal brain at ~/brain/. Queries brain_wiki (synthesized pages) first, falls back to brain_raw (raw source chunks) if confidence is low, and always reports which layer answered with a confidence score. Use when the user says "/brain-query", "search the brain", "semantic search", or asks a factual question about Harsha, his clients, projects, or any topic covered by the brain where exact slugs aren't known.
---

# brain-query

Two-tier retrieval + synthesis over `~/brain/`.

## Workflow

1. Run the query script via Bash (must `cd` into brain root for the package import to resolve):
   ```
   cd /Users/HAR5HA/brain && .venv/bin/python -m scripts.query "<question>" --k 5
   ```
2. Parse the JSON output. Important fields:
   - `layer` — `"brain_wiki"` or `"brain_raw"` — which collection answered.
   - `provider` and `threshold` — for reporting.
   - `top_score` — cosine similarity of the best result (0–1).
   - `results[]` — ordered by score. Each has `source_rel`, `heading_path`, `chunk_idx`, `text` (the chunk preview, possibly truncated to the token budget).
   - `wiki_top_if_fallback` — present only when `layer == "brain_raw"`; the nearest wiki misses, for transparency.

3. For each high-scoring result (top 2–3), Read the cited file (`~/brain/<source_rel>`) to get the full page — the preview is truncated to fit the token budget.

4. Synthesize an answer using only those sources. Cite the wiki slug when `layer == "brain_wiki"` (e.g. `[[ghl-teli-integration-flow]]`); cite `raw/<filename>` when `layer == "brain_raw"`.

5. **Always** report which layer answered, verbatim on its own line:
   - `Answered from: brain_wiki (score 0.82)`
   - `Answered from: brain_raw (score 0.71)`

6. If `layer == "brain_raw"`, offer to promote the answer into a wiki page via `/wiki-ingest` or `/wiki-save` so the next query hits the cheaper layer.

7. If `top_score < 0.60` (both layers effectively missed), fall back to training memory and label the answer `[NOT IN BRAIN — training data]` on its own line.

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
