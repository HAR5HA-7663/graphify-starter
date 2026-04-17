---
name: brain-status
description: Show health of the ~/brain/ vector store — collection counts (brain_raw, brain_wiki), indexed vs unindexed files, watcher daemon status, embedding-model consistency, and privacy-mode state. Use when the user says "/brain-status" or asks "is the brain up to date?"
---

# brain-status

## Workflow

1. Run via Bash:
   ```
   cd /Users/HAR5HA/brain && .venv/bin/python -m scripts.status
   ```
2. Show the output verbatim inside a fenced code block.

3. Escalate the following conditions with a direct recommendation:
   - **Unembedded files present** (raw_pending > 0 or wiki_pending > 0) → offer to run:
     ```
     cd /Users/HAR5HA/brain && .venv/bin/python -m scripts.reembed_all
     ```
   - **Watcher not running** (`pid: NOT RUNNING` in output) → suggest:
     ```
     launchctl unload ~/Library/LaunchAgents/com.harsha.brain-watch.plist
     launchctl load ~/Library/LaunchAgents/com.harsha.brain-watch.plist
     ```
   - **Mixed `embedding_models` in a collection** (warning line in output) → this means dim-mismatch risk; recommend:
     ```
     cd /Users/HAR5HA/brain && .venv/bin/python -m scripts.reembed_all --wipe
     ```
     Warn that querying in this state returns junk results.
   - **`PRIVACY_STRICT=on` + OpenAI model** (fatal line in output) → tell the user to either unset `BRAIN_PRIVACY_STRICT` in `~/brain/.env` OR switch `EMBED_MODEL` in `scripts/config.py` to a local provider.
   - **`hash drift` > 0** → files were edited but not re-embedded (watcher missed or was down). Offer `/brain-status` follow-up with `reembed_all`.

4. Always echo the `Recovery:` footer line from the script output so the user always sees the escape hatch.

## Rules

- Never auto-fix. Always propose the command and wait for user approval.
- Status is read-only — never write during this skill.
