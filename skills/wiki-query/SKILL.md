---
name: wiki-query
description: Answer a question strictly from the user's brain at ~/brain/wiki/. Never answer from training memory on topics the wiki covers. Always cite the page used. Use when the user says "/wiki-query", "ask the brain", or asks a factual question about their work, clients, or projects.
---

# wiki-query

Answer questions using only the wiki as source-of-truth. Training data is a last resort, and must be marked as such.

## Workflow

1. **Read `~/brain/wiki/index.md`** to identify candidate pages. Also read `~/brain/wiki/overview.md` if not already loaded.
2. **Read the relevant pages directly** from `~/brain/wiki/pages/`. Follow `[[cross-reference]]` links when needed for full context.
3. **Answer using only what's in the wiki.** Cite every claim inline with the page slug in square brackets, e.g., `[[bevri-ai]]`.
4. **If the wiki doesn't cover it:** say so explicitly. Offer either:
   - answering from general knowledge, labeled `[NOT IN WIKI — training data]`, or
   - suggesting the user drop a source in `raw/` so `/wiki-ingest` can add it.
5. **If valuable, offer to file the synthesized answer** as a new wiki page (e.g., a comparison or synthesis page) so future queries benefit.
6. **Append to `~/brain/wiki/log.md`** (append-only):
   ```
   ## [YYYY-MM-DD] query | <question>
   Pages read: <slugs>
   Notes: <gaps identified, new page offered>
   ```

## Rules

- Never answer from training memory on topics the wiki covers without reading the relevant wiki pages first.
- Every factual claim gets a citation to a wiki page slug.
- If pages disagree, surface the contradiction rather than picking one silently.
- Keep answers tight. One paragraph + bullets is usually enough.
