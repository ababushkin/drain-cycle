"""Per-ticket grade draft writer.

``drain-cycle grade-draft <issue>`` reads the most-recent run-log entry for
the given issue identifier and writes a markdown draft to
``~/.drain-cycle/grades/<issue>.md`` with ``status: draft`` and a populated
KR-check checklist.

Running the command a second time overwrites the draft (no duplicates). If no
run-log entry exists for the issue, the command exits 1 with a clear error.

The draft is also written automatically by the orchestrator when an issue
completes (final_linear_state == Done), so the operator has a pre-filled
starting point to confirm or correct without having to read raw run-log JSON.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import runlog


def grades_dir() -> Path:
    """Return the directory where per-issue grade files are written.

    Resolved on every call (not module-import time) so tests can redirect
    via ``monkeypatch.setenv("HOME", ...)`` after the module is imported.
    """
    return Path.home() / ".drain-cycle" / "grades"


def grade_path(issue_identifier: str) -> Path:
    return grades_dir() / f"{issue_identifier}.md"


def _find_most_recent_entry(
    issue_identifier: str, runs: Path
) -> dict[str, Any] | None:
    """Return the most-recent run-log entry for ``issue_identifier``, or None.

    Files are named with a UTC timestamp so reverse-lexicographic order is
    reverse-chronological. Within a file, the last matching entry is used.
    """
    files = sorted(runs.glob("*.json"), reverse=True)
    for path in files:
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for entry in reversed(payload.get("entries", [])):
            if entry.get("issue_identifier") == issue_identifier:
                return entry
    return None


def _render(issue_identifier: str, entry: dict[str, Any]) -> str:
    outcome = entry.get("final_linear_state", "unknown")
    duration = entry.get("duration_seconds")
    duration_str = f"{duration:.1f}s" if duration is not None else "—"
    exit_code = entry.get("exit_code", "—")
    model = entry.get("model") or "—"
    num_turns = entry.get("num_turns")
    num_turns_str = str(num_turns) if num_turns is not None else "—"
    cost_usd = entry.get("cost_usd")
    cost_str = f"${cost_usd:.4f}" if cost_usd is not None else "—"
    usage = entry.get("usage") or {}
    tokens_str = str(usage["cumulative"]) if "cumulative" in usage else "—"
    halt_reason = entry.get("halt_reason") or "—"
    started_at = entry.get("started_at", "—")
    finished_at = entry.get("finished_at", "—")

    lines = [
        "---",
        f"issue: {issue_identifier}",
        "status: draft",
        "---",
        "",
        "## Run summary",
        "",
        f"- outcome: {outcome}",
        f"- started_at: {started_at}",
        f"- finished_at: {finished_at}",
        f"- duration: {duration_str}",
        f"- exit_code: {exit_code}",
        f"- model: {model}",
        f"- num_turns: {num_turns_str}",
        f"- cost_usd: {cost_str}",
        f"- tokens_cumulative: {tokens_str}",
        f"- halt_reason: {halt_reason}",
        "",
        "## KR check",
        "",
        f"- [ ] KR1: outcome is Done (recorded: {outcome})",
        f"- [ ] KR2: duration is acceptable (recorded: {duration_str})",
        "- [ ] notes:",
        "",
    ]
    return "\n".join(lines)


def write_draft_from_entry(
    issue_identifier: str, entry: dict[str, Any]
) -> Path:
    """Write (or overwrite) the grade draft from a run-log entry dict.

    Creates ``~/.drain-cycle/grades/`` if absent. Returns the path written.
    """
    dest = grade_path(issue_identifier)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_render(issue_identifier, entry))
    return dest


def run(issue_identifier: str) -> int:
    """CLI entry point: find entry in run logs and write draft. Returns exit code."""
    rdir = runlog.runs_dir()
    if not rdir.is_dir():
        print(
            f"drain-cycle grade-draft: no run logs found at {rdir}",
            file=sys.stderr,
        )
        return 1

    entry = _find_most_recent_entry(issue_identifier, rdir)
    if entry is None:
        print(
            f"drain-cycle grade-draft: no run-log entry found for {issue_identifier}",
            file=sys.stderr,
        )
        return 1

    path = write_draft_from_entry(issue_identifier, entry)
    print(f"drain-cycle grade-draft: wrote {path}", file=sys.stderr)
    return 0
