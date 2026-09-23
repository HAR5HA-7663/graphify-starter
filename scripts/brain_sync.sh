#!/bin/bash
# Two-way git sync of ~/brain with a PRIVATE git remote (`origin`, branch main).
# Runs every 10 min on every device (launchd on macOS, cron on Linux, e.g. a Raspberry Pi):
#   commit local changes -> pull --rebase -> push -> (replicas) rebuild .chroma from vectors/.
# Role comes from ~/.config/brain-sync-role: "primary" on one machine (runs the watcher,
# is the only device that embeds, exports .chroma -> vectors/), "replica" elsewhere
# (imports vectors/ -> .chroma, never embeds). Markdown is edited anywhere.
# wiki/log.md merges by union (.gitattributes), so concurrent appends never conflict.
# Any other conflict aborts the rebase, keeps the local commit, logs CONFLICT and
# notifies; later runs keep retrying until it is resolved by hand.
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin"

BRAIN="${BRAIN_ROOT:-$HOME/brain}"
LOG="$BRAIN/scripts/brain_sync.log"
LOCK="$BRAIN/.brain-sync.lock.d"
ROLE="$(cat "$HOME/.config/brain-sync-role" 2>/dev/null || echo replica)"
HOST="$(hostname -s)"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }
notify() {
  command -v osascript >/dev/null && osascript -e "display notification \"$1\" with title \"brain sync\"" 2>/dev/null
  log "$1"
}
mtime() { stat -f %m "$1" 2>/dev/null || stat -c %Y "$1"; }

if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 2000000 ]; then tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; fi

if ! mkdir "$LOCK" 2>/dev/null; then
  [ $(( $(date +%s) - $(mtime "$LOCK") )) -lt 1800 ] && exit 0
  log "stale lock removed"; rm -rf "$LOCK"; mkdir "$LOCK" || exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

cd "$BRAIN" || exit 1
[ -d .git ] || { log "ERROR $BRAIN is not a git repo"; exit 1; }
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ] || [ -f .git/MERGE_HEAD ]; then
  notify "brain sync paused on $HOST: a rebase/merge is in progress in ~/brain"; exit 1
fi

if [ "$ROLE" = "primary" ]; then
  .venv/bin/python -m scripts.vector_sync export >/dev/null 2>>"$LOG" || log "WARN vector export failed"
fi

git add -A
git diff --cached --quiet || git commit -q -m "sync($HOST): $(date '+%Y-%m-%d %H:%M')" || log "WARN commit failed"

if ! git pull -q --rebase origin main 2>>"$LOG"; then
  git rebase --abort 2>/dev/null
  notify "CONFLICT syncing brain on $HOST — local commit kept; resolve with: cd ~/brain && git pull --rebase origin main"
  exit 1
fi
git push -q origin HEAD:main 2>>"$LOG" || { log "WARN push failed (will retry next run)"; exit 1; }

if [ "$ROLE" != "primary" ]; then
  out="$(.venv/bin/python -m scripts.vector_sync import 2>>"$LOG")" || log "WARN vector import failed"
  case "$out" in *imported*) log "vectors imported: $out";; esac
fi
exit 0
