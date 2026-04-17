#!/usr/bin/env bash
# Graphify installer — builds the personal brain vector layer under ~/brain/
# and wires Claude Code skills globally. Idempotent: safe to re-run.
set -euo pipefail

REPO_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BRAIN="$HOME/brain"
CLAUDE_DIR="$HOME/.claude"
SKILLS_DIR="$CLAUDE_DIR/skills"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
USERNAME="$(id -un)"
PLIST_LABEL="com.${USERNAME}.brain-watch"
PLIST_PATH="$LAUNCH_AGENTS/${PLIST_LABEL}.plist"

echo "==> Graphify installer"
echo "    Repo:    $REPO_DIR"
echo "    Brain:   $BRAIN"
echo "    User:    $USERNAME"
echo

# --- 1. Pre-flight ---------------------------------------------------------
if [[ "$(uname)" != "Darwin" ]]; then
  echo "!! Only macOS is supported (launchd daemon uses launchctl)."
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "!! python3 not found. Install Python 3.11 or 3.12 first."
  exit 1
fi
PY_MAJOR_MINOR=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
case "$PY_MAJOR_MINOR" in
  3.11|3.12) ;;
  *) echo "!! Python $PY_MAJOR_MINOR detected; need 3.11 or 3.12."; exit 1;;
esac

# --- 2. Directory layout ---------------------------------------------------
mkdir -p "$BRAIN/raw" "$BRAIN/wiki/pages" "$BRAIN/assets" "$BRAIN/scripts"
mkdir -p "$SKILLS_DIR" "$LAUNCH_AGENTS"

# --- 3. Copy code (force overwrite of scripts; preserve user wiki) ---------
cp "$REPO_DIR/pyproject.toml" "$BRAIN/pyproject.toml"
cp "$REPO_DIR/requirements.txt" "$BRAIN/requirements.txt"
cp "$REPO_DIR/.gitignore" "$BRAIN/.gitignore"
cp "$REPO_DIR"/scripts/*.py "$BRAIN/scripts/"

# Seed wiki + raw only if empty (don't clobber existing content)
if [[ ! -f "$BRAIN/wiki/index.md" ]]; then
  cp "$REPO_DIR/wiki/index.md" "$BRAIN/wiki/index.md"
fi
if [[ ! -f "$BRAIN/wiki/overview.md" ]]; then
  cp "$REPO_DIR/wiki/overview.md" "$BRAIN/wiki/overview.md"
fi
if [[ ! -f "$BRAIN/wiki/log.md" ]]; then
  cp "$REPO_DIR/wiki/log.md" "$BRAIN/wiki/log.md"
fi
if [[ ! -f "$BRAIN/raw/README.md" ]]; then
  cp "$REPO_DIR/raw/README.md" "$BRAIN/raw/README.md"
fi

# --- 4. Python venv + deps -------------------------------------------------
if [[ ! -d "$BRAIN/.venv" ]]; then
  echo "==> Creating venv"
  python3 -m venv "$BRAIN/.venv"
fi
"$BRAIN/.venv/bin/pip" install --upgrade pip -q
"$BRAIN/.venv/bin/pip" install -q -r "$BRAIN/requirements.txt"

# --- 5. .env (prompt if missing) -------------------------------------------
if [[ ! -f "$BRAIN/.env" ]]; then
  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    printf "OPENAI_API_KEY=%s\n" "$OPENAI_API_KEY" > "$BRAIN/.env"
  else
    echo
    echo "==> OPENAI_API_KEY not found."
    echo "    Option A: rerun this script with OPENAI_API_KEY=sk-... ./install.sh"
    echo "    Option B: write it yourself:"
    echo "             printf 'OPENAI_API_KEY=sk-...\\n' > ~/brain/.env && chmod 600 ~/brain/.env"
    echo
    echo "    Skipping backfill + daemon load until .env is in place."
  fi
fi
[[ -f "$BRAIN/.env" ]] && chmod 600 "$BRAIN/.env"

# --- 6. Skills (global, user-scope) ----------------------------------------
for skill in wiki-ingest wiki-query wiki-lint wiki-save brain-query brain-status; do
  mkdir -p "$SKILLS_DIR/$skill"
  cp "$REPO_DIR/skills/$skill/SKILL.md" "$SKILLS_DIR/$skill/SKILL.md"
done

# --- 7. Global CLAUDE.md merge (between markers) ---------------------------
CLAUDE_MD="$CLAUDE_DIR/CLAUDE.md"
touch "$CLAUDE_MD"
SNIPPET="$(sed "s/USERNAME/${USERNAME}/g" "$REPO_DIR/claude/CLAUDE.md.snippet")"

if grep -q "<!-- graphify:start -->" "$CLAUDE_MD" 2>/dev/null; then
  # Replace existing block
  python3 - <<PYEOF
import re, pathlib
p = pathlib.Path("$CLAUDE_MD")
current = p.read_text()
block = """${SNIPPET//\"/\\\"}"""
new = re.sub(r"<!-- graphify:start -->.*?<!-- graphify:end -->", block, current, flags=re.DOTALL)
p.write_text(new)
PYEOF
else
  { echo; echo "$SNIPPET"; echo; } >> "$CLAUDE_MD"
fi

# --- 8. launchd plist (template -> real) -----------------------------------
sed -e "s/USERNAME/${USERNAME}/g" -e "s|HOMEDIR|${HOME}|g" \
  "$REPO_DIR/launchd/com.USERNAME.brain-watch.plist.tmpl" > "$PLIST_PATH"

# --- 9. brain-fs MCP server (user scope) -----------------------------------
if command -v claude >/dev/null 2>&1; then
  if ! claude mcp list 2>/dev/null | grep -q "brain-fs"; then
    echo "==> Registering brain-fs MCP (user scope)"
    claude mcp add --scope user brain-fs -- npx -y @modelcontextprotocol/server-filesystem "$BRAIN" || true
  fi
else
  echo "!! 'claude' CLI not found — skipping MCP registration."
  echo "   Install Claude Code, then run: claude mcp add --scope user brain-fs -- npx -y @modelcontextprotocol/server-filesystem $BRAIN"
fi

# --- 10. Backfill + daemon (only if .env present) --------------------------
if [[ -f "$BRAIN/.env" ]] && grep -q "OPENAI_API_KEY=sk-" "$BRAIN/.env" 2>/dev/null; then
  echo "==> Backfilling existing wiki + raw into Chroma"
  ( cd "$BRAIN" && "$BRAIN/.venv/bin/python" -m scripts.reembed_all ) || echo "!! backfill failed — check logs and rerun."

  echo "==> Loading launchd agent"
  launchctl unload "$PLIST_PATH" 2>/dev/null || true
  launchctl load "$PLIST_PATH"
fi

# --- 11. Summary -----------------------------------------------------------
cat <<DONE

==> Done.

Layout:        $BRAIN
Plist:         $PLIST_PATH
Skills:        $SKILLS_DIR/{wiki-ingest,wiki-query,wiki-lint,wiki-save,brain-query,brain-status}
CLAUDE.md:     $CLAUDE_MD  (graphify block between markers)

Next:
  1. If .env was not created, write OPENAI_API_KEY there and rerun this script.
  2. Test status:
       cd ~/brain && .venv/bin/python -m scripts.status
  3. Test query:
       cd ~/brain && .venv/bin/python -m scripts.query "hello world"
  4. From Claude Code:  /brain-status   /brain-query <q>   /wiki-ingest   /wiki-save

Manage daemon:
  launchctl unload $PLIST_PATH && launchctl load $PLIST_PATH
  launchctl list | grep brain-watch

DONE
