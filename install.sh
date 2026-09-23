#!/usr/bin/env bash
# Graphify installer — builds the personal brain under ~/brain/ and wires it into
# Claude Code (skills, CLAUDE.md block, SessionStart hook, brain-fs MCP, launchd watcher).
# Idempotent: re-running updates code and skills, never your wiki, journal or .env.
#
#   ./install.sh                                  core install (primary machine)
#   ./install.sh --daily-sync                     + nightly transcript distillation job
#   ./install.sh --sync-remote <private-git-url>  + multi-device git sync of ~/brain
#   ./install.sh --sync-remote <url> --replica    this Mac is a replica (pulls vectors, never embeds)
#   ./install.sh --no-hook                        skip the SessionStart context hook
set -euo pipefail

REPO_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BRAIN="$HOME/brain"
CLAUDE_DIR="$HOME/.claude"
SKILLS_DIR="$CLAUDE_DIR/skills"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
USERNAME="$(id -un)"
SKILLS=(wiki-ingest wiki-query wiki-lint wiki-save brain-query brain-status)

DAILY_SYNC=0; SYNC_REMOTE=""; ROLE="primary"; HOOK=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --daily-sync) DAILY_SYNC=1 ;;
    --sync-remote) SYNC_REMOTE="${2:?--sync-remote needs a git URL}"; shift ;;
    --replica) ROLE="replica" ;;
    --no-hook) HOOK=0 ;;
    -h|--help) sed -n 2,11p "$0"; exit 0 ;;
    *) echo "!! unknown option $1"; exit 2 ;;
  esac
  shift
done

echo "==> Graphify installer  (brain: $BRAIN, user: $USERNAME, role: $ROLE)"

# --- 1. Pre-flight ---------------------------------------------------------
[[ "$(uname)" == "Darwin" ]] || { echo "!! macOS only (launchd). For a Linux replica use linux/install-replica.sh"; exit 1; }
PY=""
for cand in python3.12 python3.11 python3; do
  command -v "$cand" >/dev/null || continue
  case "$("$cand" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')" in
    3.11|3.12) PY="$(command -v "$cand")"; break ;;
  esac
done
[[ -n "$PY" ]] || { echo "!! need Python 3.11 or 3.12 (e.g. brew install python@3.12)"; exit 1; }

# --- 2. Layout + code --------------------------------------------------------
mkdir -p "$BRAIN"/{raw,wiki/pages,assets,scripts/tests,journal,archive} "$SKILLS_DIR" "$LAUNCH_AGENTS"
cp "$REPO_DIR"/{pyproject.toml,requirements.txt} "$BRAIN/"
cp "$REPO_DIR"/scripts/*.py "$REPO_DIR"/scripts/*.sh "$REPO_DIR"/scripts/daily_sync_prompt.md "$BRAIN/scripts/"
cp "$REPO_DIR"/scripts/tests/*.py "$BRAIN/scripts/tests/"
chmod +x "$BRAIN"/scripts/*.sh
cp "$REPO_DIR/templates/brain.gitignore" "$BRAIN/.gitignore"
cp "$REPO_DIR/templates/brain.gitattributes" "$BRAIN/.gitattributes"
for f in wiki/index.md wiki/overview.md wiki/log.md raw/README.md; do
  [[ -f "$BRAIN/$f" ]] || cp "$REPO_DIR/$f" "$BRAIN/$f"
done
# Obsidian vault config (graph view on) — only if the brain is not already a vault
if [[ ! -d "$BRAIN/.obsidian" ]]; then
  mkdir -p "$BRAIN/.obsidian" && cp "$REPO_DIR"/templates/obsidian/*.json "$BRAIN/.obsidian/"
fi

# --- 3. Python venv ----------------------------------------------------------
[[ -d "$BRAIN/.venv" ]] || { echo "==> Creating venv"; "$PY" -m venv "$BRAIN/.venv"; }
"$BRAIN/.venv/bin/pip" install -q --upgrade pip
"$BRAIN/.venv/bin/pip" install -q -r "$BRAIN/requirements.txt"

# --- 4. .env -----------------------------------------------------------------
if [[ ! -f "$BRAIN/.env" ]]; then
  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    printf "OPENAI_API_KEY=%s\n" "$OPENAI_API_KEY" > "$BRAIN/.env"
    [[ -n "${TYPESAFE_API_KEY:-}" ]] && printf "TYPESAFE_API_KEY=%s\n" "$TYPESAFE_API_KEY" >> "$BRAIN/.env"
  elif [[ "$ROLE" == "primary" ]]; then
    echo "!! OPENAI_API_KEY not set — write it to ~/brain/.env (chmod 600) and re-run. Skipping backfill + watcher."
  fi
fi
[[ -f "$BRAIN/.env" ]] && chmod 600 "$BRAIN/.env"
HAVE_KEY=0; grep -q "^OPENAI_API_KEY=sk-" "$BRAIN/.env" 2>/dev/null && HAVE_KEY=1

# --- 5. Skills + CLAUDE.md block + SessionStart hook ---------------------------
for s in "${SKILLS[@]}"; do mkdir -p "$SKILLS_DIR/$s"; cp "$REPO_DIR/skills/$s/SKILL.md" "$SKILLS_DIR/$s/SKILL.md"; done

python3 - "$CLAUDE_DIR/CLAUDE.md" "$REPO_DIR/claude/CLAUDE.md.snippet" "$USERNAME" <<'PY'
import pathlib, re, sys
target, snippet, user = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
block = snippet.read_text().replace("USERNAME", user).strip()
current = target.read_text() if target.exists() else ""
if "<!-- graphify:start -->" in current:
    new = re.sub(r"<!-- graphify:start -->.*?<!-- graphify:end -->", lambda _: block, current, flags=re.S)
else:
    new = current.rstrip() + ("\n\n" if current.strip() else "") + block + "\n"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(new)
PY

if [[ "$HOOK" == "1" ]]; then
  python3 - "$CLAUDE_DIR/settings.json" "$BRAIN/scripts/session_context.sh" <<'PY'
import json, pathlib, sys
path, script = pathlib.Path(sys.argv[1]), sys.argv[2]
cfg = json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}
groups = cfg.setdefault("hooks", {}).setdefault("SessionStart", [])
cmd = f"bash {script}"
if not any(h.get("command") == cmd for g in groups for h in g.get("hooks", [])):
    groups.append({"hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    print("==> SessionStart hook added to ~/.claude/settings.json")
PY
fi

# --- 6. brain-fs MCP (user scope) ---------------------------------------------
if command -v claude >/dev/null 2>&1; then
  claude mcp list 2>/dev/null | grep -q "brain-fs" || \
    claude mcp add --scope user brain-fs -- npx -y @modelcontextprotocol/server-filesystem "$BRAIN" || true
else
  echo "!! 'claude' CLI not found — skipping brain-fs MCP registration."
fi

# --- 7. launchd agents ----------------------------------------------------------
install_agent() {   # $1 = template name (without .plist.tmpl)
  local label="${1/USERNAME/$USERNAME}" plist
  plist="$LAUNCH_AGENTS/$label.plist"
  sed -e "s/USERNAME/${USERNAME}/g" -e "s|HOMEDIR|${HOME}|g" "$REPO_DIR/launchd/$1.plist.tmpl" > "$plist"
  launchctl unload "$plist" 2>/dev/null || true
  launchctl load "$plist"
  echo "==> loaded $label"
}

if [[ "$ROLE" == "primary" && "$HAVE_KEY" == "1" ]]; then
  echo "==> Backfilling wiki + raw into Chroma"
  ( cd "$BRAIN" && .venv/bin/python -m scripts.reembed_all ) || echo "!! backfill failed — check output and re-run."
  install_agent com.USERNAME.brain-watch
fi
[[ "$DAILY_SYNC" == "1" ]] && install_agent com.USERNAME.daily-brain-sync

# --- 8. Optional multi-device git sync ------------------------------------------
if [[ -n "$SYNC_REMOTE" ]]; then
  mkdir -p "$HOME/.config" && echo "$ROLE" > "$HOME/.config/brain-sync-role"
  cd "$BRAIN"
  if [[ ! -d .git ]]; then
    git init -q -b main
    git remote add origin "$SYNC_REMOTE"
  fi
  if git ls-remote --exit-code origin main >/dev/null 2>&1; then
    git fetch -q origin
    if ! git rev-parse -q --verify HEAD >/dev/null; then   # fresh device: take the remote as-is
      git reset -q origin/main && git checkout -q -- .
    fi
  else                                                      # first device: publish
    [[ "$ROLE" == "primary" && "$HAVE_KEY" == "1" ]] && .venv/bin/python -m scripts.vector_sync export >/dev/null
    git add -A && git commit -q -m "brain: initial import" && git push -q -u origin main
  fi
  bash scripts/brain_sync.sh || echo "!! first sync failed — see ~/brain/scripts/brain_sync.log"
  [[ "$ROLE" == "replica" ]] && .venv/bin/python -m scripts.vector_sync import --force >/dev/null
  install_agent com.USERNAME.brain-sync
fi

cat <<DONE

==> Done.
  Brain:     $BRAIN   (open it as an Obsidian vault for the graph view)
  Skills:    $SKILLS_DIR/{$(IFS=,; echo "${SKILLS[*]}")}
  Status:    cd ~/brain && .venv/bin/python -m scripts.status
  Query:     cd ~/brain && .venv/bin/python -m scripts.query "hello world"
  In Claude: /brain-status   /brain-query <q>   /wiki-ingest   /wiki-save   /wiki-lint
DONE
