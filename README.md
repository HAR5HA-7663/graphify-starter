# graphify-starter

A personal "second brain" for Claude Code: a markdown wiki you can open in Obsidian, backed by two ChromaDB vector collections, kept in sync by a file watcher, queried through Claude Code skills. It can also distil your Claude Code sessions into the wiki every night and sync the brain between machines through a private git repo.

This repo is the setup only. It ships no one's data: `wiki/pages/` starts empty.

**macOS** for the main machine (launchd). Linux machines such as a Raspberry Pi can run as sync replicas. Python 3.11 or 3.12.

## What you get

| Piece | What it does |
|---|---|
| `~/brain/` | `raw/` drop zone (read-only for the assistant), `wiki/` synthesized pages with `[[links]]`, `journal/` + `archive/` (local only) |
| Two vector layers | `brain_wiki` is queried first (curated, small), `brain_raw` is the fallback (exhaustive, noisier) |
| Watcher | launchd agent that embeds any markdown change within 2 s; hash-dedup makes unchanged files free; atomic saves (temp file + rename) are handled |
| Fast queries | a query daemon keeps Chroma loaded, so a query costs about one embedding round-trip (~0.5 s instead of ~2 s). It starts on demand, exits after 15 min idle, and restarts itself when code, `.env` or settings change |
| Skills | `/brain-query`, `/brain-status`, `/wiki-ingest`, `/wiki-query`, `/wiki-lint`, `/wiki-save` |
| Session context | a `SessionStart` hook injects `wiki/index.md` + `overview.md` into every Claude Code session |
| Obsidian | `~/brain` is set up as a vault with the graph view on. The graph shows pages and their `[[links]]`; the vectors are a separate index built from the same files |
| Jev layer (optional) | with a [TypeSafe](https://typesafe.ai) key: reranks ambiguous queries, flags contradictions and sensitive content before an ingest, and gives each page its own staleness interval. Advisory only, fails open, never sees pages tagged `private` |
| Daily sync (optional) | a nightly headless Claude run that turns the day's session transcripts into a journal entry, wiki updates and auto-memory, with timeouts, retries and backups |
| Multi-device sync (optional) | a private git repo shared by all your machines: markdown both ways every 10 min, vectors exported as text so other machines rebuild their index without API calls |
| Safety rails | privacy-strict mode, pinned deps, per-file size cap, embedding model tracked per chunk with mixed-model warnings, `private` tag, journal/archive/.env never leave the machine |

## Quick start

```bash
git clone https://github.com/HAR5HA-7663/graphify-starter
cd graphify-starter
OPENAI_API_KEY=sk-... ./install.sh
```

Optional extras:

```bash
TYPESAFE_API_KEY=...  # add to ~/brain/.env to turn on the Jev layer
./install.sh --daily-sync                                   # nightly session distillation (17:30)
./install.sh --sync-remote git@github.com:you/brain.git     # multi-device sync (repo must be PRIVATE)
```

Or hand `prompt-for-claude.md` to Claude Code and let it run the install.

The installer:

1. creates `~/brain/` and a venv with pinned deps,
2. copies the scripts, skills (`~/.claude/skills/`), a marked block in `~/.claude/CLAUDE.md` and the `SessionStart` hook in `~/.claude/settings.json`,
3. registers the `brain-fs` filesystem MCP server,
4. backfills anything already in the wiki and loads the watcher,
5. with the flags above, also loads the daily-sync and git-sync agents.

Re-running is safe: it updates code and skills and never touches your wiki, journal or `.env`. Use `--no-hook` to skip the hook.

## Multi-device sync

One machine is the **primary**: it runs the watcher, is the only one that calls the embedding API, and exports `.chroma` to `vectors/` as text (one JSONL file per page, base64 float32 embeddings, so only changed pages show up in a commit). Every other machine is a **replica**: it pulls, pushes its markdown edits, and rebuilds its own `.chroma` from `vectors/` when that changes. Replicas need no OpenAI key.

```bash
# primary (first machine) — publishes ~/brain to the empty private repo
./install.sh --sync-remote git@github.com:you/brain.git
# another Mac
./install.sh --sync-remote git@github.com:you/brain.git --replica
# a Linux box / Raspberry Pi
./linux/install-replica.sh git@github.com:you/brain.git
```

Every 10 minutes: commit → `pull --rebase` → push. `wiki/log.md` merges by union, so appends from two machines never conflict. Any other conflict stops that machine's sync with a notification, keeps its commit, and retries until you resolve it. `journal/`, `archive/`, `.env` and logs are never committed.

Chroma's own files are not put in git: they are binary, rewritten on every change and can't be merged.

## Privacy

- `BRAIN_PRIVACY_STRICT=1` in `~/brain/.env` blocks OpenAI embeddings and every Jev call; scripts fail closed. You then need a local embedding provider in `scripts/config.py`.
- Tag a page `private` in its frontmatter to keep it out of every Jev request.
- The daily sync keeps personal-life detail in `journal/` (local) and out of the wiki.
- Wiki page text is sent to OpenAI for embedding, and to TypeSafe for Jev checks when enabled.

## Tuning

`~/brain/scripts/config.py`:

| Constant | Default | What it does |
|---|---|---|
| `EMBED_MODEL` | `text-embedding-3-small` | Embedding model (OpenAI models trip privacy-strict) |
| `CHUNK_MAX_TOKENS` / `CHUNK_OVERLAP_TOKENS` | 800 / 100 | Heading-aware chunking |
| `CONFIDENCE_THRESHOLDS` | `{openai: 0.45, local: 0.55}` | Wiki → raw fallback gate |
| `TRAINING_FALLBACK_FLOOR` | 0.40 | Below this, the verdict is `not_in_brain` |
| `RERANK_BAND` | 0.30–0.50 | Cosine range where Jev is consulted |
| `QUERY_CONTEXT_BUDGET_TOKENS` | 1500 | Token budget for returned previews |
| `QUERY_DAEMON_IDLE_S` | 900 | Query daemon idle exit |
| `MAX_FILE_MB` | 50 | Files above this are skipped |
| `WATCH_DEBOUNCE_SECONDS` | 2.0 | Per-path debounce in the watcher |

Env switches: `BRAIN_JEV=off`, `BRAIN_QUERY_DAEMON=off`, `BRAIN_CONFIDENCE_THRESHOLD=0.5`, `BRAIN_ROOT=/other/path`. Daily sync: `SYNC_MODEL`, `SYNC_HOUR`/`SYNC_MINUTE`, `SYNC_EXTRA_TOOLS` (read-only MCP tools for a "Life" section), `JEV_TRIAGE=0`. Session triage areas: `~/brain/triage_areas.json`.

## Usage

In Claude Code, from any directory:

```
/brain-query what did we decide about the billing retry logic?
/brain-status
/wiki-ingest raw/some-doc.md
/wiki-save
/wiki-lint
```

From a shell:

```bash
cd ~/brain
.venv/bin/python -m scripts.query "your question" --k 5
.venv/bin/python -m scripts.status
.venv/bin/python -m scripts.reembed_all [--wipe]
.venv/bin/python -m scripts.staleness            # Jev-scored stale pages
.venv/bin/python -m scripts.ingest_check raw/x.md
.venv/bin/python -m scripts.vector_sync export|import
.venv/bin/python -m unittest discover -s scripts/tests -t .
FORCE_NOW=1 bash scripts/daily_session_sync.sh   # distil sessions up to now
```

## Uninstall

```bash
for a in brain-watch brain-sync daily-brain-sync; do
  launchctl unload ~/Library/LaunchAgents/com.$(id -un).$a.plist 2>/dev/null
  rm -f ~/Library/LaunchAgents/com.$(id -un).$a.plist
done
claude mcp remove --scope user brain-fs
rm -rf ~/.claude/skills/{wiki-ingest,wiki-query,wiki-lint,wiki-save,brain-query,brain-status}
# remove the graphify block from ~/.claude/CLAUDE.md and the session_context.sh hook from ~/.claude/settings.json
# keep or delete ~/brain/ as you like
```

## Repo layout

```
install.sh                 idempotent installer (macOS)
linux/install-replica.sh   sync replica for Linux / Raspberry Pi
prompt-for-claude.md       paste-to-Claude bootstrap prompt
scripts/                   Python package, run as `python -m scripts.<name>`
  config.py chunker.py embedder.py fastembed.py store.py embed.py reembed_all.py
  watch.py status.py query.py query_client.py query_daemon.py
  jev.py rerank.py pages.py ingest_check.py staleness.py session_triage.py
  vector_sync.py brain_sync.sh session_context.sh daily_session_sync.sh daily_sync_prompt.md
  tests/
skills/                    six Claude Code skills
claude/CLAUDE.md.snippet   merged into ~/.claude/CLAUDE.md between markers
launchd/                   watcher, git-sync and daily-sync agent templates
templates/                 ~/brain .gitignore/.gitattributes and Obsidian vault config
wiki/ raw/ assets/         empty seeds
```

## Credits

Built on Andrej Karpathy's LLM-Wiki pattern. The vector layer, watcher, skills, Jev layer and sync are this repo's contribution.
