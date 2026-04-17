---
name: wiki-lint
description: Health check on ~/brain/wiki/. Find orphan pages, broken cross-reference links, stale last_updated dates, missing frontmatter fields, concepts mentioned repeatedly without a dedicated page, and contradictions between pages. Use when the user says "/wiki-lint" or "lint the wiki."
---

# wiki-lint

Scan the wiki and report problems. Don't auto-fix — propose fixes and wait for confirmation.

## Scope

- Directory: `~/brain/wiki/pages/`
- Plus: `~/brain/wiki/index.md`, `overview.md`, `log.md`

## Checks

1. **Orphan pages** — any page with zero inbound `[[slug]]` references from other pages or from `index.md`.
2. **Broken links** — any `[[slug]]` that doesn't resolve to an existing page file.
3. **Missing frontmatter** — every page must have `title`, `last_updated`, `tags`, and at least one cross-reference in the body.
4. **Stale content** — flag pages with `last_updated` older than 90 days and suggest review.
5. **Undocumented concepts** — tokens/phrases that appear 3+ times across pages without a dedicated page.
6. **Contradictions** — conflicting facts between pages (dates, roles, numbers, URLs).
7. **Index drift** — entries in `index.md` that don't match page filenames, or pages not listed in `index.md`.
8. **Log hygiene** — ensure `log.md` entries are append-only (no modifications to prior dated sections) and parseable headers.

## Output

Markdown report grouped by check:

```
# Wiki Lint Report — YYYY-MM-DD

## Orphan pages (N)
- slug — suggested fix

## Broken links (N)
- page.md → [[bad-slug]]

...
```

Append a log entry:
```
## [YYYY-MM-DD] lint | <one-line summary>
Issues found: <counts>
Notes: <top priorities>
```

## Rules

- Report-only by default. Make no edits unless the user approves each fix.
- Never delete pages even when orphaned — suggest merging or adding cross-references instead.
