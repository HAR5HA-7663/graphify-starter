---
name: wiki-save
description: Persist a synthesized answer (from /brain-query, /wiki-query, or mid-conversation) as a new wiki page at ~/brain/wiki/pages/. Writes frontmatter (title, last_updated, tags), requires at least one cross-reference, updates index.md, appends to log.md, and embeds the new page into brain_wiki. Use when the user says "/wiki-save", "save that to the brain", "file this as a page," or "remember this for later."
---

# wiki-save

## Workflow

1. **Pick a slug.** Kebab-case, short, topic-focused (e.g., `dnc-compliance`, `bevri-pricing-tiers`).
   - Check `~/brain/wiki/index.md` and `~/brain/wiki/pages/*.md` for collisions.
   - If a page with this slug already exists, switch to **update mode** — preserve existing cross-refs, bump `last_updated`, surface any contradictions in a `## Contradictions` section.

2. **Write the page** to `~/brain/wiki/pages/<slug>.md` with frontmatter:
   ```yaml
   ---
   title: <Human Title>
   last_updated: <YYYY-MM-DD>
   tags: [tag1, tag2, ...]
   ---
   ```
   - The body MUST include at least one `[[cross-reference]]` to an existing wiki page.
   - Keep the body tight — structure with H2/H3 headings so the chunker produces clean, targeted chunks.

3. **Update `~/brain/wiki/index.md`.** Add a one-line entry under the appropriate section:
   ```
   - [[<slug>]] — <one-line hook>
   ```

4. **Append to `~/brain/wiki/log.md`** (never edit prior entries):
   ```
   ## [YYYY-MM-DD] wiki-save | <short title>
   Source: <conversation | /brain-query result | /wiki-query result | user dictation>
   Pages created: <slug>     (or Pages updated: <slug>)
   Notes: <contradictions, open questions>
   ```

5. **Embed into brain_wiki.** Run:
   ```
   cd /Users/HAR5HA/brain && .venv/bin/python -m scripts.embed /Users/HAR5HA/brain/wiki/pages/<slug>.md --collection brain_wiki
   ```
   Idempotent — safe to re-run. The launchd watcher will also catch the write and re-embed ~2s later; running explicitly guarantees the vector is live before the user's next `/brain-query`.

6. **Report** the created slug + cross-refs back to the user:
   ```
   Saved: [[<slug>]]  →  <wiki/pages/slug.md>
   Refs:  [[<a>]], [[<b>]]
   Embedded into brain_wiki.
   ```

## Rules

- Never write to `~/brain/raw/` from this skill.
- Never create a page without a `[[cross-reference]]` in the body — pages must be findable.
- If the synthesized content has low confidence or was sourced from training memory (`[NOT IN BRAIN]` tag), tell the user before saving — they may not want to codify a guess.
- If `/brain-status` shows a mixed-model warning, refuse the embed and tell the user to run `reembed_all.py --wipe` first.
