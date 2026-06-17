"""Draft-handling tests for ``drain-cycle grade``.

Pins the AC item: un-confirmed drafts are reported as warnings on stderr
and not counted in the pass-rate denominator.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drain_cycle import grade


def _write_grade_file(grades_dir: Path, issue: str, status: str) -> None:
    grades_dir.mkdir(parents=True, exist_ok=True)
    (grades_dir / f"{issue}.md").write_text(
        f"---\nissue: {issue}\nstatus: {status}\n---\n"
    )


def _write_run_entry(
    runs_dir: Path,
    issue: str,
    final_state: str = "Done",
    outcome_verdict: object = None,
) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"run-{issue}-20260522T100000000000Z.json").write_text(
        json.dumps(
            {
                "cycle_id": "cycle-1",
                "cycle_duration_seconds": 10.0,
                "entries": [
                    {
                        "issue_identifier": issue,
                        "started_at": "2026-05-22T10:00:00+00:00",
                        "finished_at": "2026-05-22T10:05:00+00:00",
                        "exit_code": 0,
                        "final_linear_state": final_state,
                        "worktree_path": f"/tmp/.worktrees/{issue}",
                        "halt_reason": None,
                        "outcome_verdict": outcome_verdict,
                    }
                ],
            }
        )
        + "\n"
    )


_PASS_VERDICT = {"result": "pass", "findings": []}


def test_draft_file_warns_on_stderr_and_not_counted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    _write_grade_file(grades_dir, "ABA-10", "draft")

    exit_code = grade.run(grades_dir, runs_dir)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "ABA-10" in captured.err
    assert "draft" in captured.err.lower()
    assert "graded: 0" in captured.out


def test_mix_confirmed_and_draft_only_confirmed_counted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    _write_grade_file(grades_dir, "ABA-1", "confirmed")
    _write_run_entry(runs_dir, "ABA-1", outcome_verdict=_PASS_VERDICT)
    _write_grade_file(grades_dir, "ABA-2", "draft")
    _write_grade_file(grades_dir, "ABA-3", "draft")

    exit_code = grade.run(grades_dir, runs_dir)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "graded: 1" in captured.out
    # Both drafts warned.
    assert "ABA-2" in captured.err
    assert "ABA-3" in captured.err


def test_draft_not_checked_for_silent_done_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A draft file whose run-log entry would be a violation must NOT
    trigger exit 1 — drafts are excluded from grading entirely."""
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    _write_grade_file(grades_dir, "ABA-5", "draft")
    _write_run_entry(runs_dir, "ABA-5", final_state="Done", outcome_verdict=None)

    exit_code = grade.run(grades_dir, runs_dir)

    assert exit_code == 0


def test_unreadable_grade_file_warns_and_continues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    grades_dir.mkdir()
    bad = grades_dir / "ABA-BAD.md"
    bad.write_text("---\nissue: ABA-GOOD\nstatus: confirmed\n---\n")
    bad.chmod(0o000)  # make unreadable

    _write_grade_file(grades_dir, "ABA-GOOD", "confirmed")
    _write_run_entry(runs_dir, "ABA-GOOD", outcome_verdict=_PASS_VERDICT)

    try:
        exit_code = grade.run(grades_dir, runs_dir)
    finally:
        bad.chmod(0o644)  # restore so tmp_path cleanup works

    captured = capsys.readouterr()
    # The bad file warned on stderr.
    assert "ABA-BAD" in captured.err or "unreadable" in captured.err.lower()
    # The good file still counted.
    assert "graded: 1" in captured.out
    assert exit_code == 0
