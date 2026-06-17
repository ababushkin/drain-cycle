"""Output-format tests for ``drain-cycle grade``.

Pins the three required output lines: ``graded: N``, ``pass-rate: N/D (P%)``,
and ``silent-Done violations: none | <issue-id ...>``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drain_cycle import grade


def _write_grade_file(grades_dir: Path, issue: str, status: str) -> None:
    grades_dir.mkdir(parents=True, exist_ok=True)
    path = grades_dir / f"{issue}.md"
    path.write_text(f"---\nissue: {issue}\nstatus: {status}\n---\n")


def _write_run_entry(
    runs_dir: Path,
    issue: str,
    final_state: str = "Done",
    outcome_verdict: object = None,
) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"run-{issue}-20260522T100000000000Z.json"
    path.write_text(
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


def test_graded_line_reflects_confirmed_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    for issue in ("ABA-1", "ABA-2", "ABA-3"):
        _write_grade_file(grades_dir, issue, "confirmed")
        _write_run_entry(runs_dir, issue, outcome_verdict=_PASS_VERDICT)

    grade.run(grades_dir, runs_dir)

    out = capsys.readouterr().out
    assert "graded: 3" in out


def test_pass_rate_format_N_slash_D_pct(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    # 2 clean, 1 silent-Done violation → 2/3 (67%)
    for issue in ("ABA-1", "ABA-2"):
        _write_grade_file(grades_dir, issue, "confirmed")
        _write_run_entry(runs_dir, issue, outcome_verdict=_PASS_VERDICT)
    _write_grade_file(grades_dir, "ABA-3", "confirmed")
    _write_run_entry(runs_dir, "ABA-3", outcome_verdict=None)  # violation

    grade.run(grades_dir, runs_dir)

    out = capsys.readouterr().out
    assert "pass-rate: 2/3 (67%)" in out


def test_pass_rate_100_pct_when_all_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    for issue in ("ABA-1", "ABA-2"):
        _write_grade_file(grades_dir, issue, "confirmed")
        _write_run_entry(runs_dir, issue, outcome_verdict=_PASS_VERDICT)

    grade.run(grades_dir, runs_dir)

    out = capsys.readouterr().out
    assert "pass-rate: 2/2 (100%)" in out


def test_no_violations_line_present_on_clean_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    _write_grade_file(grades_dir, "ABA-1", "confirmed")
    _write_run_entry(runs_dir, "ABA-1", outcome_verdict=_PASS_VERDICT)

    grade.run(grades_dir, runs_dir)

    out = capsys.readouterr().out
    assert "silent-Done violations: none" in out


def test_violation_line_lists_issue_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grades_dir = tmp_path / "grades"
    runs_dir = tmp_path / "runs"
    _write_grade_file(grades_dir, "ABA-99", "confirmed")
    _write_run_entry(runs_dir, "ABA-99", final_state="Done", outcome_verdict=None)

    grade.run(grades_dir, runs_dir)

    out = capsys.readouterr().out
    assert "silent-Done violations:" in out
    assert "ABA-99" in out
