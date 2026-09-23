---
name: wiki-ingest
description: Ingest a file from ~/brain/raw/ into the wiki. Extract key concepts and entities, create or update pages in ~/brain/wiki/pages/, add cross-references, append to log.md. Use when the user says "/wiki-ingest", "ingest raw/...", or drops a file in raw/ and asks to process it.
---

# wiki-ingest

Turn a raw dropped file into structured wiki pages.

## Input

The user provides a path inside `~/brain/raw/` (PDF, markdown, screenshot, link, transcript, etc.). If no path is given, list the newest files in `~/brain/raw/` and ask which to ingest.

## Workflow

1. **Read the source** from `~/brain/raw/<path>`. Never write into `raw/`.
2. **Extract** key concepts, entities (people, companies, products, orgs), facts, claims, dates, URLs. Note any numbers or commitments.
3. **Discuss 3–5 key takeaways** with the user before writing, unless the user said "just ingest, no discussion."
4. **Check existing pages** in `~/brain/wiki/pages/` and `~/brain/wiki/index.md`. Prefer updating over creating duplicates. Match by slug and by aliases in frontmatter.
5. **Create or update pages** in `~/brain/wiki/pages/<slug>.md`. Every page must have:
   - `title`
   - `last_updated` (today's date, ISO)
   - `tags` (list)
   - At least one cross-reference (`[[other-slug]]`) to an existing page.
6. **Flag contradictions** with existing pages explicitly in the page body under a `## Contradictions` heading. Do not silently overwrite. Don't rely on noticing them while reading — run the checker on the source before writing pages (step 5), and again on any page you drafted from scratch:
   ```
   cd ~/brain && .venv/bin/python -m scripts.ingest_check raw/<path>.md
   ```
   It compares every chunk with its nearest existing wiki chunks (a few seconds, markdown only) and returns JSON:
   - `conflicts[]` — each names the existing page + heading and gives both excerpts with a probability `p`. Read both passages; every real conflict **must** land under `## Contradictions` on the affected page. Dismiss a flag only after reading it (restatements and added detail are the usual false positives).
   - `sensitive[]` — `financial_detail`, `third_party_private_data`, `credentials`. Do **not** promote that content into the wiki: summarise without the figures/identifiers, and tell the user what was left out. Financial account detail never goes into the wiki.
   - `skipped` — privacy-strict mode, Jev off, or the file is tagged `private`. Proceed on your own careful reading, as before.
   The checker is advisory: an empty result is not proof there are no contradictions.
7. **Update `~/brain/wiki/index.md`** — add one-line entries for any new pages; update `last_updated`.
8. **Append to `~/brain/wiki/log.md`** (never edit prior entries):
   ```
   ## [YYYY-MM-DD] ingest | <short title>
   Source: raw/<path>
   Pages created: <slugs>
   Pages updated: <slugs>
   Notes: <contradictions, open questions, follow-ups>
   ```
9. **Attachments:** binary assets referenced from pages should be copied to `~/brain/assets/` and linked with relative paths. Never copy into `raw/`.
10. **Embed into brain_wiki.** For each wiki page created or updated in step 5, run:
    ```
    cd ~/brain && .venv/bin/python -m scripts.embed ~/brain/wiki/pages/<slug>.md --collection brain_wiki
    ```
    Idempotent (skips if content unchanged). The launchd watcher also catches the write, but running explicitly guarantees the vector is live before the user's next `/brain-query`.
    Note: raw/ files are auto-embedded into `brain_raw` by the watcher on drop — this skill does not embed raw.

## Rules

- `raw/` is read-only. Never write there.
- One ingest should typically touch 3–10 wiki pages (create or update).
- Set confidence qualifiers in-line ("per source X", "unverified") when claims aren't directly stated.
- Prefer one source at a time; batch ingests degrade cross-referencing.
- Do not delete pages. Mark deprecated in frontmatter (`deprecated: true`) instead.
