# Prompt: replicate this graphify brain on my machine

Paste this whole message into Claude Code after cloning the repo. It assumes you're on macOS, have `python3` (3.11 or 3.12), and have Claude Code installed.

---

I have cloned the graphify-starter repo to my machine. Set it up end-to-end:

1. From the repo root, run `./install.sh`. This creates `~/brain/`, a Python venv, installs deps, writes skills into `~/.claude/skills/`, merges the graphify block into `~/.claude/CLAUDE.md`, installs the launchd plist at `~/Library/LaunchAgents/com.<myusername>.brain-watch.plist`, and registers the `brain-fs` filesystem MCP server under user scope.

2. If the installer warns that `~/brain/.env` is missing, I'll paste my OpenAI API key below — use the Write tool to put it there with chmod 600:

```
OPENAI_API_KEY=sk-REPLACE_ME_PASTE_HERE
```

3. After `.env` is in place, run `./install.sh` again so it does the backfill and loads the daemon.

4. Verify end-to-end:
   - `cd ~/brain && .venv/bin/python -m scripts.status` — expect two collections listed, watcher PID present.
   - `cd ~/brain && .venv/bin/python -m scripts.query "hello"` — expect JSON with a `layer` field.
   - `launchctl list | grep brain-watch` — expect a PID.
   - In this Claude Code session, test `/brain-status` and `/brain-query`.

5. Show me the final folder tree under `~/brain/` and report any skipped verification steps with the reason.

6. Explain in two sentences how I drop new notes into the brain (short: put markdown in `~/brain/raw/` and the watcher embeds it; use `/wiki-ingest` to synthesize raw sources into wiki pages; use `/wiki-save` to file a synthesized answer).

Rules:
- Do not invent wiki content for me — leave `wiki/pages/` empty, seed only `index.md`, `overview.md`, `log.md` from the repo defaults.
- Do not commit my `.env` or `.chroma/` anywhere.
- If the `claude` CLI isn't available, print the manual `claude mcp add` command and continue.
- If I'm not on macOS, stop and say so — the launchd plist is macOS-only.
