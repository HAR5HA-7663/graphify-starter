# graphify-starter

Portable build of a personal Claude Code "second brain" — a markdown wiki backed by two ChromaDB vector collections (`brain_wiki`, `brain_raw`) with a launchd watcher that keeps the index in sync as files change.

**macOS only.** Python 3.11 or 3.12 required.

## What you get

- `~/brain/` — the knowledge directory. Drop sources in `raw/`, synthesized notes live in `wiki/pages/`.
- **Two vector layers.** `brain_wiki` is queried first (high-quality, small). `brain_raw` is the fallback (exhaustive, noisier).
- **Auto-embedding daemon.** Any markdown change under `raw/` or `wiki/pages/` is embedded within 2 seconds. Hash-dedup makes re-runs free.
- **Claude Code skills.** `/brain-query`, `/brain-status`, `/wiki-ingest`, `/wiki-query`, `/wiki-lint`, `/wiki-save` — all globally installed.
- **Safety rails.** Version-pinned deps, privacy-strict mode, per-file 50 MB cap, embedding-model tracked in chunk metadata, mixed-model warnings.

## Quick start

```bash
git clone <this-repo> graphify-starter
cd graphify-starter
OPENAI_API_KEY=sk-... ./install.sh
```

That one command:

1. Creates `~/brain/` with the standard layout.
2. Creates `~/brain/.venv/` and installs pinned deps.
3. Writes `~/brain/.env` (chmod 600) with your key.
4. Copies `scripts/` into `~/brain/scripts/`.
5. Copies all six skills into `~/.claude/skills/`.
6. Merges the graphify block into `~/.claude/CLAUDE.md` (between `<!-- graphify:start --> ... <!-- graphify:end -->` markers — safe to re-run).
7. Installs `~/Library/LaunchAgents/com.<username>.brain-watch.plist` and loads it.
8. Registers the `brain-fs` filesystem MCP server under user scope (via `claude mcp add`).
9. Runs `scripts.reembed_all` to backfill anything already in `~/brain/wiki/`.

Re-running `./install.sh` is idempotent — it only overwrites scripts/skills (never your wiki content or `.env`).

## Without the API key upfront

```bash
./install.sh               # installs everything except daemon + backfill
printf "OPENAI_API_KEY=sk-...\n" > ~/brain/.env && chmod 600 ~/brain/.env
./install.sh               # now does backfill + loads launchd
```

## Privacy strict mode

To block all OpenAI calls (scripts fail closed at import):

```bash
echo "BRAIN_PRIVACY_STRICT=1" >> ~/brain/.env
```

You'll then need to swap `EMBED_MODEL` in `~/brain/scripts/config.py` to a local provider (out of scope for v1 — future work).

## Tuning knobs

Edit `~/brain/scripts/config.py`:

| Constant | Default | What it does |
|---|---|---|
| `EMBED_MODEL` | `text-embedding-3-small` | OpenAI model. Any `text-embedding-*` triggers privacy-strict. |
| `CHUNK_MAX_TOKENS` | 800 | Heading-aware chunk size ceiling. |
| `CHUNK_OVERLAP_TOKENS` | 100 | Sliding-window overlap when a section exceeds max. |
| `CONFIDENCE_THRESHOLDS` | `{openai: 0.75, local: 0.55}` | Per-provider wiki→raw fallback threshold. Override via `BRAIN_CONFIDENCE_THRESHOLD=` env or `--threshold`. |
| `QUERY_CONTEXT_BUDGET_TOKENS` | 1500 | Max tokens across returned chunk previews. |
| `MAX_FILE_MB` | 50 | Embed skips files larger than this. |
| `WATCH_DEBOUNCE_SECONDS` | 2.0 | Per-path coalesce window in the daemon. |

## Usage

In Claude Code (any directory):

```
/brain-query What's my integration flow look like?
/brain-status
/wiki-ingest raw/some-doc.md
/wiki-save
```

From a shell:

```
cd ~/brain
.venv/bin/python -m scripts.query "your question" --k 5
.venv/bin/python -m scripts.status
.venv/bin/python -m scripts.reembed_all          # backfill
.venv/bin/python -m scripts.reembed_all --wipe   # nuke + rebuild
.venv/bin/python -m scripts.embed wiki/pages/<slug>.md --collection brain_wiki --dry-run
```

Drop a new source:

```
cp some-pdf-or-notes.md ~/brain/raw/
# watcher embeds it within ~2s. Check with:
tail -n 5 ~/brain/scripts/watch.log
```

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.$(id -un).brain-watch.plist
rm ~/Library/LaunchAgents/com.$(id -un).brain-watch.plist
claude mcp remove --scope user brain-fs   # if Claude CLI installed
# Remove the graphify block from ~/.claude/CLAUDE.md manually (between markers).
rm -rf ~/.claude/skills/{wiki-ingest,wiki-query,wiki-lint,wiki-save,brain-query,brain-status}
# Keep or delete ~/brain/ at your discretion.
```

## Repo layout

```
.
├── README.md
├── install.sh                         # idempotent installer
├── prompt-for-claude.md               # paste-to-Claude bootstrap prompt
├── pyproject.toml
├── requirements.txt
├── .env.example
├── .gitignore
├── scripts/                           # Python package — runs as `python -m scripts.<name>`
│   ├── __init__.py
│   ├── config.py
│   ├── chunker.py
│   ├── embedder.py
│   ├── store.py
│   ├── embed.py
│   ├── query.py
│   ├── status.py
│   ├── reembed_all.py
│   └── watch.py
├── skills/
│   ├── brain-query/SKILL.md
│   ├── brain-status/SKILL.md
│   ├── wiki-ingest/SKILL.md
│   ├── wiki-lint/SKILL.md
│   ├── wiki-query/SKILL.md
│   └── wiki-save/SKILL.md
├── claude/
│   └── CLAUDE.md.snippet              # merged into ~/.claude/CLAUDE.md between markers
├── launchd/
│   └── com.USERNAME.brain-watch.plist.tmpl
├── wiki/
│   ├── index.md
│   ├── overview.md
│   ├── log.md
│   └── pages/                         # starts empty
├── raw/
│   └── README.md
└── assets/
```

## How to hand this to a friend

1. Push this repo to GitHub (your fork, public or private).
2. Give the friend the URL and `prompt-for-claude.md`.
3. They clone, paste the prompt into Claude Code, and Claude runs `install.sh` for them.

## Credits

Built on Andrej Karpathy's LLM-Wiki pattern (April 2026). Vector layer + watcher + skills are this repo's contribution.
