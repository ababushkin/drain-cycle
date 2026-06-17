"""``drain-cycle grade``: initiative health from confirmed grade files.

Reads every grade file at ``~/.drain-cycle/grades/*.md``, separates
confirmed from draft, and reports:

1. Total tickets graded (confirmed count).
2. Pass-rate: confirmed entries without a silent-Done violation,
   expressed as ``N/D (P%)``.
3. Silent-Done violations: confirmed Done entries where the outcome
   verifier never ran (``outcome_verdict == null`` in the run log).
4. Draft files are warned about on stderr and excluded from the count.

A *silent-Done violation* is the condition the halt-on-fail gate (ABA-328)
is supposed to prevent: a ticket reaches Done in Linear without the outcome
verifier running. Only ``outcome_verdict == null`` counts — a verdict of any
kind (pass or fail) clears the violation.

Exit code: 0 if no violations; 1 if any silent-Done violation is found.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import grade_draft, runlog


def default_grades_dir() -> Path:
    return grade_draft.grades_dir()


def default_runs_dir() -> Path:
    return runlog.runs_dir()


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Return key/value pairs from the opening ``---`` block.

    Parses only simple ``key: value`` lines — no nested YAML, no lists.
    Returns an empty dict if the file has no frontmatter block.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    result: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def _find_run_entry(issue_identifier: str, runs_dir: Path) -> dict[str, Any] | None:
    """Return the most-recent run-log entry for *issue_identifier*, or None."""
    if not runs_dir.is_dir():
        return None
    for path in sorted(runs_dir.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for entry in reversed(payload.get("entries", [])):
            if entry.get("issue_identifier") == issue_identifier:
                return entry
    return None


def _is_silent_done(entry: dict[str, Any]) -> bool:
    """True when the entry is Done in Linear but outcome_verdict was never set."""
    return (
        entry.get("final_linear_state") == "Done"
        and entry.get("outcome_verdict") is None
    )


def run(grades_dir: Path, runs_dir: Path) -> int:
    """Read grade files, report pass-rate and silent-Done violations.

    Returns exit code: 0 (clean) or 1 (violations found).
    """
    confirmed: list[str] = []
    drafts: list[str] = []

    if grades_dir.is_dir():
        for path in sorted(grades_dir.glob("*.md")):
            try:
                fm = _parse_frontmatter(path.read_text())
            except OSError as exc:
                print(
                    f"drain-cycle grade: skipping unreadable grade file {path}: {exc}",
                    file=sys.stderr,
                )
                continue
            issue = fm.get("issue", path.stem)
            status = fm.get("status", "draft")
            if status == "confirmed":
                confirmed.append(issue)
            else:
                drafts.append(issue)

    if drafts:
        for issue in drafts:
            print(
                f"drain-cycle grade: warning: {issue} is a draft — excluded from rate",
                file=sys.stderr,
            )

    total = len(confirmed)

    violations: list[str] = []
    no_runlog: list[str] = []

    for issue in confirmed:
        entry = _find_run_entry(issue, runs_dir)
        if entry is None:
            no_runlog.append(issue)
            continue
        if _is_silent_done(entry):
            violations.append(issue)

    for issue in no_runlog:
        print(
            f"drain-cycle grade: warning: {issue} confirmed but no run-log entry found",
            file=sys.stderr,
        )

    passes = total - len(violations) - len(no_runlog)
    pass_rate_denom = total - len(no_runlog)

    if pass_rate_denom > 0:
        pct = round(passes * 100 / pass_rate_denom)
        pass_rate_str = f"{passes}/{pass_rate_denom} ({pct}%)"
    else:
        pass_rate_str = "n/a"

    print(f"graded: {total}")
    print(f"pass-rate: {pass_rate_str}")

    if violations:
        print("silent-Done violations:")
        for issue in violations:
            print(f"  {issue}")
    else:
        print("silent-Done violations: none")

    return 1 if violations else 0
