"""Silent-Done violation and exit-code tests for ``drain-cycle grade``.

Pins the core safety guarantee: a confirmed Done entry with
``outcome_verdict == null`` triggers exit 1 and is listed by issue ID.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drain_cycle import grade


def _write_grade_file(grades_dir: Path, issue: str, status: str = "confirmed") -> None:
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


_PASS_VERDICT = {"result": "pass", "findings": [], "invoked_at": "2026-05-22T10:05:00+00:00"}
_FAIL_VERDICT = {"result": "fail", "findings": ["not met"], "invoked_at": "2026-05-22T10:05:00+00:00"}


def test_confirmed_done_with_null_verdict_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    _write_grade_file(grades_dir, "ABA-1")
    _write_run_entry(runs_dir, "ABA-1", final_state="Done", outcome_verdict=None)

    exit_code = grade.run(grades_dir, runs_dir)

    assert exit_code != 0


def test_confirmed_done_with_pass_verdict_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    _write_grade_file(grades_dir, "ABA-2")
    _write_run_entry(runs_dir, "ABA-2", final_state="Done", outcome_verdict=_PASS_VERDICT)

    exit_code = grade.run(grades_dir, runs_dir)

    assert exit_code == 0


def test_confirmed_done_with_fail_verdict_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fail verdict is not a silent-Done — the verifier ran. Not a violation."""
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    _write_grade_file(grades_dir, "ABA-3")
    _write_run_entry(runs_dir, "ABA-3", final_state="Done", outcome_verdict=_FAIL_VERDICT)

    exit_code = grade.run(grades_dir, runs_dir)

    assert exit_code == 0


def test_confirmed_not_done_with_null_verdict_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only Done entries can be silent-Done violations; a halted entry is not."""
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    _write_grade_file(grades_dir, "ABA-4")
    _write_run_entry(runs_dir, "ABA-4", final_state="In Progress", outcome_verdict=None)

    exit_code = grade.run(grades_dir, runs_dir)

    assert exit_code == 0


def test_multiple_violations_all_listed_by_issue_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    for issue in ("ABA-10", "ABA-20"):
        _write_grade_file(grades_dir, issue)
        _write_run_entry(runs_dir, issue, final_state="Done", outcome_verdict=None)

    exit_code = grade.run(grades_dir, runs_dir)

    out = capsys.readouterr().out
    assert exit_code != 0
    assert "ABA-10" in out
    assert "ABA-20" in out


def test_violation_listed_but_clean_entries_not_in_violation_block(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    _write_grade_file(grades_dir, "ABA-CLEAN")
    _write_run_entry(runs_dir, "ABA-CLEAN", outcome_verdict=_PASS_VERDICT)
    _write_grade_file(grades_dir, "ABA-DIRTY")
    _write_run_entry(runs_dir, "ABA-DIRTY", outcome_verdict=None)

    grade.run(grades_dir, runs_dir)

    out = capsys.readouterr().out
    violations_block = out.split("silent-Done violations:", 1)[1]
    assert "ABA-DIRTY" in violations_block
    assert "ABA-CLEAN" not in violations_block


def test_silent_done_exact_condition_null_not_missing_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The violation requires outcome_verdict to be null (Python None).
    An entry where the key is absent must also be treated as null."""
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    _write_grade_file(grades_dir, "ABA-5")
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / "run-ABA-5-20260522T100000000000Z.json").write_text(
        json.dumps(
            {
                "cycle_id": "cycle-1",
                "cycle_duration_seconds": 0.0,
                "entries": [
                    {
                        "issue_identifier": "ABA-5",
                        "started_at": "2026-05-22T10:00:00+00:00",
                        "finished_at": "2026-05-22T10:05:00+00:00",
                        "exit_code": 0,
                        "final_linear_state": "Done",
                        "worktree_path": "/tmp/.worktrees/ABA-5",
                        "halt_reason": None,
                        # outcome_verdict key is absent entirely
                    }
                ],
            }
        )
        + "\n"
    )

    exit_code = grade.run(grades_dir, runs_dir)

    assert exit_code != 0
