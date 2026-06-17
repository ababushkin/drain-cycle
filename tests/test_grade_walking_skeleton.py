"""Walking-skeleton tests for the new ``drain-cycle grade`` (grade-file mode).

Pins the two exit paths and the minimal happy path:
- No grades dir / empty dir → graded: 0, exit 0.
- One confirmed file with a clean run-log entry → exit 0.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drain_cycle import grade


def _write_grade_file(grades_dir: Path, issue: str, status: str) -> Path:
    grades_dir.mkdir(parents=True, exist_ok=True)
    path = grades_dir / f"{issue}.md"
    path.write_text(f"---\nissue: {issue}\nstatus: {status}\n---\n\n## KR check\n\n- [x] KR1\n")
    return path


def _write_run_entry(
    runs_dir: Path,
    issue: str,
    final_state: str = "Done",
    outcome_verdict: object = None,
) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"cycle-{issue}-20260522T100000000000Z.json"
    path.write_text(
        json.dumps(
            {
                "cycle_id": f"cycle-{issue}",
                "cycle_duration_seconds": 10.0,
                "entries": [
                    {
                        "issue_identifier": issue,
                        "started_at": "2026-05-22T10:00:00+00:00",
                        "finished_at": "2026-05-22T10:05:00+00:00",
                        "exit_code": 0,
                        "final_linear_state": final_state,
                        "worktree_path": f"/tmp/repo/.worktrees/{issue}",
                        "halt_reason": None,
                        "outcome_verdict": outcome_verdict,
                    }
                ],
            }
        )
        + "\n"
    )
    return path


def test_grade_missing_grades_dir_exits_zero_with_zero_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "grades"
    runs_dir = tmp_path / "runs"

    exit_code = grade.run(missing, runs_dir)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "graded: 0" in captured.out


def test_grade_empty_grades_dir_exits_zero_with_zero_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    grades_dir.mkdir()
    runs_dir = tmp_path / "runs"

    exit_code = grade.run(grades_dir, runs_dir)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "graded: 0" in captured.out


def test_grade_one_confirmed_clean_entry_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    _write_grade_file(grades_dir, "ABA-1", "confirmed")
    _write_run_entry(
        runs_dir,
        "ABA-1",
        final_state="Done",
        outcome_verdict={"result": "pass", "findings": [], "invoked_at": "2026-05-22T10:05:00+00:00"},
    )

    exit_code = grade.run(grades_dir, runs_dir)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "graded: 1" in captured.out
    assert "violations: none" in captured.out


def test_grade_reports_no_violation_line_when_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    _write_grade_file(grades_dir, "ABA-1", "confirmed")
    _write_run_entry(
        runs_dir,
        "ABA-1",
        final_state="Done",
        outcome_verdict={"result": "pass", "findings": []},
    )

    exit_code = grade.run(grades_dir, runs_dir)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "ABA-1" not in captured.out
