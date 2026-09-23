#!/bin/bash
# Daily brain sync — distils the day's Claude Code session transcripts into the brain
# wiki (~/brain/wiki), a local journal (~/brain/journal, never synced) and the
# per-project auto-memory dirs (~/.claude/projects/*/memory/), via a headless Claude run.
#
# Scheduling (launchd agent com.<user>.daily-brain-sync, installed by install.sh --daily-sync):
#   - fires daily at SYNC_HOUR:SYNC_MINUTE (default 17:30); runs missed while asleep fire on wake.
#   - RunAtLoad fires it at every login; the anacron-style stamp below makes that a no-op
#     unless a scheduled run was missed.
#
# Force a run regardless of the stamp:  FORCE=1 bash daily_session_sync.sh
# Catch up to this minute (ad-hoc):    FORCE_NOW=1 bash daily_session_sync.sh
# Skip the Jev triage pass:             JEV_TRIAGE=0 bash daily_session_sync.sh
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin"

BRAIN="${BRAIN_ROOT:-$HOME/brain}"
STAMP="$BRAIN/scripts/.daily-sync-last-run"          # mtime = end of the last covered window
LOCKDIR="$BRAIN/scripts/.daily-sync.lock.d"
LOG="$BRAIN/scripts/daily_sync.log"
PROMPT_FILE="$BRAIN/scripts/daily_sync_prompt.md"
SESSIONS_LIST="$BRAIN/scripts/.daily-sync-sessions.txt"
CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude || echo "$HOME/.local/bin/claude")}"
MODEL="${SYNC_MODEL:-sonnet}"
SYNC_HOUR="${SYNC_HOUR:-17}"; SYNC_MINUTE="${SYNC_MINUTE:-30}"
# Extra tools the distiller may use, e.g. read-only Slack/calendar MCPs for a "Life" section:
#   SYNC_EXTRA_TOOLS="mcp__slack__*,mcp__calendar__*"
EXTRA_TOOLS="${SYNC_EXTRA_TOOLS:-}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }
notify() { command -v osascript >/dev/null && osascript -e "display notification \"$2\" with title \"$1\"" 2>/dev/null; true; }

if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 5242880 ]; then tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; fi

# ---- due check: run iff the last success predates the most recent boundary ----
NOW=$(date +%s)
BOUNDARY=$(date -j -f "%Y-%m-%d %H:%M" "$(date +%Y-%m-%d) $SYNC_HOUR:$SYNC_MINUTE" +%s 2>/dev/null \
           || date -d "today $SYNC_HOUR:$SYNC_MINUTE" +%s)
if [ "$NOW" -ge "$BOUNDARY" ]; then DUE="$BOUNDARY"; else DUE=$((BOUNDARY - 86400)); fi
if [ "${FORCE_NOW:-0}" = "1" ]; then FORCE=1; DUE="$NOW"; fi
LAST=0
[ -f "$STAMP" ] && LAST=$(stat -f %m "$STAMP" 2>/dev/null || stat -c %Y "$STAMP")
if [ "${FORCE:-0}" != "1" ] && [ "$LAST" -ge "$DUE" ]; then exit 0; fi

mkdir "$LOCKDIR" 2>/dev/null || { log "SKIP another sync is already running"; exit 0; }
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

# ---- window: (last covered boundary, this boundary]; widens backward after missed runs ----
if [ -f "$STAMP" ]; then START_EPOCH="$LAST"; else START_EPOCH=$((DUE - 86400)); fi
WIN_START="$BRAIN/scripts/.win-start"; WIN_END="$BRAIN/scripts/.win-end"
touch -t "$(date -r "$START_EPOCH" +%Y%m%d%H%M.%S 2>/dev/null || date -d "@$START_EPOCH" +%Y%m%d%H%M.%S)" "$WIN_START"
touch -t "$(date -r "$DUE" +%Y%m%d%H%M.%S 2>/dev/null || date -d "@$DUE" +%Y%m%d%H%M.%S)" "$WIN_END"

# Skip this job's own sessions (cwd = the brain), memory dirs and subagent transcripts.
OWN_PROJECT="$(printf '%s' "$BRAIN" | sed 's#[/.]#-#g')"
find "$HOME/.claude/projects" -name '*.jsonl' ! -name 'agent-*' \
     -newer "$WIN_START" ! -newer "$WIN_END" 2>/dev/null \
  | grep -v "/$OWN_PROJECT/" | grep -v '/memory/' > "$SESSIONS_LIST"

# ---- backup of wiki + journal + memories BEFORE the run mutates them (keep last 7) ----
BK="$BRAIN/archive/backups"; mkdir -p "$BK"
tar czf "$BK/brain-backup-$(date +%Y-%m-%d).tar.gz" -C "$BRAIN" wiki journal 2>/dev/null
ls -t "$BK"/brain-backup-*.tar.gz 2>/dev/null | tail -n +8 | xargs rm -f 2>/dev/null

# ---- optional Jev triage: drop transcripts with nothing durable in them ----
# Fail-safe: a transcript Jev cannot score is kept; if triage fails, the full list is used.
TRIAGE_JSON="$BRAIN/scripts/.daily-sync-triage.json"
RAW_COUNT=$(wc -l < "$SESSIONS_LIST" | tr -d ' ')
rm -f "$TRIAGE_JSON"
if [ "$RAW_COUNT" -gt 0 ] && [ "${JEV_TRIAGE:-1}" = "1" ]; then
  if "$BRAIN/.venv/bin/python" -m scripts.session_triage "$SESSIONS_LIST" --out "$TRIAGE_JSON" > "$SESSIONS_LIST.kept" 2>>"$LOG"; then
    log "TRIAGE kept $(wc -l < "$SESSIONS_LIST.kept" | tr -d ' ') of $RAW_COUNT transcript(s)"
    mv "$SESSIONS_LIST.kept" "$SESSIONS_LIST"
  else
    log "TRIAGE unavailable — distilling all $RAW_COUNT transcript(s)"
    rm -f "$SESSIONS_LIST.kept" "$TRIAGE_JSON"
  fi
fi

COUNT=$(wc -l < "$SESSIONS_LIST" | tr -d ' ')
if [ "$COUNT" -eq 0 ]; then
  log "DONE nothing to sync (0 transcripts in window)"
  touch -t "$(date -r "$DUE" +%Y%m%d%H%M.%S 2>/dev/null || date -d "@$DUE" +%Y%m%d%H%M.%S)" "$STAMP"
  exit 0
fi
[ -x "$CLAUDE_BIN" ] || { log "FAIL claude CLI not found at $CLAUDE_BIN"; exit 1; }

log "START distilling $COUNT transcript(s) (model=$MODEL)"
RUN_START=$(date +%s)
cd "$BRAIN" || exit 1

PROMPT_TMP="$BRAIN/scripts/.daily-sync-prompt-run.txt"
{ cat "$PROMPT_FILE"; echo
  echo "Transcript list for this run ($COUNT files): $SESSIONS_LIST"
  if [ -f "$TRIAGE_JSON" ]; then
    echo "Triage report (per-session knowledge 0-3, area, 'sensitive' probability): $TRIAGE_JSON"
    echo "For any session with sensitive >= 0.5: distil the facts WITHOUT account numbers, balances or other people's identifiers, and never promote that detail into the wiki."
  fi
  echo "Time window for this run: $(date -r "$START_EPOCH" '+%Y-%m-%d %H:%M' 2>/dev/null || date -d "@$START_EPOCH" '+%Y-%m-%d %H:%M') -> $(date -r "$DUE" '+%Y-%m-%d %H:%M' 2>/dev/null || date -d "@$DUE" '+%Y-%m-%d %H:%M')"
  [ -n "$EXTRA_TOOLS" ] && echo "Life sources enabled for this run (read-only): $EXTRA_TOOLS"
} > "$PROMPT_TMP"

# Hard wall-clock cap per attempt: `claude -p` can block for hours on a stalled request.
ATTEMPT_TIMEOUT=${ATTEMPT_TIMEOUT:-5400}; MAX_ATTEMPTS=${MAX_ATTEMPTS:-3}; RETRY_BACKOFF=${RETRY_BACKOFF:-600}
TOOLS="Read,Glob,Grep,Write,Edit,Bash,ToolSearch${EXTRA_TOOLS:+,$EXTRA_TOOLS}"

run_claude_once() {
  "$CLAUDE_BIN" -p --model "$MODEL" --allowedTools "$TOOLS" < "$PROMPT_TMP" >> "$LOG" 2>&1 &
  local pid=$! waited=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge "$ATTEMPT_TIMEOUT" ]; then
      kill -TERM "$pid" 2>/dev/null; sleep 10; kill -KILL "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
      return 124
    fi
    sleep 15; waited=$((waited + 15))
  done
  wait "$pid"
}

RC=1
for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  run_claude_once; RC=$?
  [ "$RC" -eq 0 ] && break
  log "ATTEMPT $attempt/$MAX_ATTEMPTS failed (rc=$RC)"
  [ "$attempt" -lt "$MAX_ATTEMPTS" ] && sleep "$RETRY_BACKOFF"
done
rm -f "$PROMPT_TMP"

ELAPSED=$(( $(date +%s) - RUN_START ))
if [ "$RC" -eq 0 ]; then
  touch -t "$(date -r "$DUE" +%Y%m%d%H%M.%S 2>/dev/null || date -d "@$DUE" +%Y%m%d%H%M.%S)" "$STAMP"
  log "DONE ok in ${ELAPSED}s"
  notify "Daily brain sync" "synced $COUNT session(s) in ${ELAPSED}s — see scripts/daily_sync_report.md"
else
  log "FAIL claude exited $RC after ${ELAPSED}s — stamp NOT updated, retries at next boundary/login"
  notify "Daily brain sync failed" "claude exited $RC — see scripts/daily_sync.log"
fi
exit "$RC"
