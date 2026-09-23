"""Jev triage for the daily brain sync: which transcripts deserve a distillation pass.

    .venv/bin/python -m scripts.session_triage <sessions.list> [--out triage.json]

For every transcript path in the list, condenses the conversation (user turns in full,
assistant turns trimmed, tool noise dropped) and asks Jev in one request:

  knowledge   score 0..3  how much durable, reusable knowledge the session holds
  area        choice      where it belongs (work project vs personal vs tooling)
  sensitive   noul        bank / immigration / third-party private detail present
  decisions   noul        contains a decision, correction or preference from the user

Writes a JSON report and prints a filtered list of the transcripts worth distilling
(knowledge >= KEEP_MIN or decisions >= 0.5) to stdout, one path per line, so the sync
script can feed only those to Claude. Sessions that Jev could not score are KEPT —
the triage only ever removes what it has positively judged empty. Under
BRAIN_PRIVACY_STRICT or BRAIN_JEV=off every transcript is kept and no call is made.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import click

from . import config, jev

KEEP_MIN = 1.0
CONDENSED_CHARS = 9000
# Where a session belongs. Override with ~/brain/triage_areas.json ({"name": "description"}),
# e.g. one entry per client or project, so the report routes knowledge to the right place.
AREAS = {
    "work": "the user's job or client work: repos, deployments, tickets, colleagues",
    "personal_admin": "personal life admin: immigration, banking, loans, insurance, housing, travel",
    "tooling": "the user's own machine, Claude Code setup, hooks, brain/wiki, scripts, side projects",
    "other": "none of the above",
}
_AREAS_FILE = config.BRAIN_ROOT / "triage_areas.json"
if _AREAS_FILE.exists():
    try:
        AREAS = {**json.loads(_AREAS_FILE.read_text()), "other": "none of the above"}
    except ValueError:
        pass


def condense(path: Path) -> tuple[str, int]:
    """Readable transcript: user text in full, assistant text trimmed, tools as one-liners."""
    parts: list[str] = []
    turns = 0
    for line in path.read_text(errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        msg = rec.get("message") or {}
        role = msg.get("role") or rec.get("type")
        content = msg.get("content")
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            blocks = content
        else:
            continue
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                text = " ".join(b["text"].split())
                if role == "user":
                    if text.startswith("<") and text.endswith(">"):  # system-reminder / hook noise
                        continue
                    parts.append(f"USER: {text[:1500]}")
                    turns += 1
                else:
                    parts.append(f"ASSISTANT: {text[:400]}")
            elif b.get("type") == "tool_use":
                name = b.get("name", "tool")
                inp = b.get("input") or {}
                hint = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                parts.append(f"TOOL {name}: {str(hint)[:120]}")
    text = "\n".join(parts)
    if len(text) > CONDENSED_CHARS:  # keep the start and the end: goals up top, outcomes at the bottom
        text = text[: CONDENSED_CHARS // 2] + "\n[...]\n" + text[-CONDENSED_CHARS // 2:]
    return text, turns


def score(path: Path) -> dict:
    text, turns = condense(path)
    row = {"path": str(path), "user_turns": turns, "chars": len(text)}
    if turns == 0:
        row.update(knowledge=0.0, area="other", sensitive=0.0, decisions=0.0, basis="empty", keep=False)
        return row
    if not jev.enabled():
        row.update(basis="jev off", keep=True)
        return row
    state = ("Condensed transcript of one Claude Code session belonging to the user, a developer. "
             "It will be distilled into their personal knowledge wiki.\n\n" + text)
    questions = {
        "knowledge": {"type": "score", "criteria": [
            "Nothing durable: routine commands, a quick fix, chit-chat, or an abandoned start",
            "A small fact or two worth a line in an existing page",
            "Real findings, decisions or state changes worth updating a wiki page",
            "Substantial new knowledge: an investigation, an incident, a design, a new project or a personal-life event"],
            "instructions": "How much durable, reusable knowledge about the user's work, projects, tools or life does this session contain?"},
        "area": {"type": "choice", "criteria": AREAS, "instructions": "Which area does this session mainly belong to?"},
        "sensitive": {"type": "noul", "instructions":
            "Does the session contain bank/card/loan account details, balances or transactions, immigration case details, "
            "or private information about a person other than the user (address, ID numbers, health, salary)?"},
        "decisions": {"type": "noul", "instructions":
            "Does the user state a decision, a correction of the assistant, or a preference about how they want work done?"},
    }
    try:
        a = jev.ask(state, questions, purpose="session_triage")
        k = float((a.get("knowledge") or {}).get("score", 0))
        d = jev.noul(a, "decisions") or 0.0
        row.update(knowledge=round(k, 2), knowledge_conf=round(float((a.get("knowledge") or {}).get("confidence", 0)), 2),
                   area=(a.get("area") or {}).get("choice", "other"), sensitive=round(jev.noul(a, "sensitive") or 0.0, 2),
                   decisions=round(d, 2), basis="jev", keep=(k >= KEEP_MIN or d >= 0.5))
    except jev.JevError as e:
        row.update(basis=f"jev failed: {e}", keep=True)
    return row


@click.command()
@click.argument("sessions_list", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path), default=None, help="where to write the JSON report")
def main(sessions_list: Path, out: Path | None) -> None:
    paths = [Path(p) for p in sessions_list.read_text().split("\n") if p.strip() and Path(p).exists()]
    with ThreadPoolExecutor(max_workers=config.JEV_WORKERS) as pool:
        rows = list(pool.map(score, paths))
    kept = [r for r in rows if r["keep"]]
    report = {"total": len(rows), "kept": len(kept), "skipped": len(rows) - len(kept),
              "sensitive": [r["path"] for r in rows if r.get("sensitive", 0) >= 0.5],
              "by_area": {a: sum(1 for r in kept if r.get("area") == a) for a in AREAS},
              "rows": rows}
    if out:
        out.write_text(json.dumps(report, indent=1))
    for r in kept:
        click.echo(r["path"])
    click.echo(f"triage: {len(kept)}/{len(rows)} kept, {len(report['sensitive'])} flagged sensitive", err=True)


if __name__ == "__main__":
    main()
