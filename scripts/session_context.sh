#!/bin/bash
# SessionStart hook — auto-inject the brain catalog (index) + synthesis
# (overview) into every Claude Code session's context, so context doesn't have
# to be re-explained each session.
#
# Emits the documented SessionStart contract:
#   {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}
# Uses system python3 (always present on macOS) to build JSON safely — does NOT
# depend on the brain .venv (which can break independently).
#
# FAIL-OPEN: any error -> emit nothing, exit 0. A broken brain must never block
# a session from starting.
set -uo pipefail

WIKI="${BRAIN_ROOT:-$HOME/brain}/wiki"

/usr/bin/python3 - "$WIKI" <<'PY' 2>/dev/null || true
import json, sys, pathlib

wiki = pathlib.Path(sys.argv[1])
parts = []
for name in ("index.md", "overview.md"):
    p = wiki / name
    if p.exists():
        try:
            parts.append(f"===== ~/brain/wiki/{name} =====\n" +
                         p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            pass

if not parts:
    sys.exit(0)  # nothing to inject; emit no output

header = (
    "# Personal brain — auto-loaded at session start\n"
    "Below is the catalog (index) + high-level synthesis of the user's knowledge "
    "wiki at ~/brain/. Treat it as background context about the user, their work, "
    "clients, and projects. For detail, read ~/brain/wiki/pages/<slug>.md or run "
    "/brain-query for semantic lookup. Do NOT Grep ~/brain if the brain can answer.\n\n"
)
ctx = header + "\n\n".join(parts)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": ctx,
    }
}))
PY
