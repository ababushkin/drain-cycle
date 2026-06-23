"""Aggregate-statistics tests for ``drain-cycle scorecard``.

Pins the SUMMARY block and the per-cycle summary row: average cost and
duration (per cycle and per issue), the fully-executed rate (a halted or
nonzero-exit run is excluded), retry rate, and cost per correct issue.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drain_cycle import scorecard


_PASS_VERDICT = {"result": "pass", "findings": [], "invoked_at": "2026-05-22T10:05:00+00:00"}
_GO_VERDICT = {"result": "go", "findings": [], "invocation_id": "abc"}


def _write_run(
    runs_dir: Path,
    cycle_id: str,
    entries: list[dict],
    filename: str,
    cycle_duration_seconds: float | None = None,
) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "cycle_id": cycle_id,
        "cycle_duration_seconds": cycle_duration_seconds,
        "cycle_halt_reason": None,
        "entries": entries,
    }
    (runs_dir / filename).write_text(json.dumps(payload) + "\n")


def _entry(
    issue: str,
    outcome: dict | None = _PASS_VERDICT,
    review: dict | None = _GO_VERDICT,
    *,
    cost: float = 0.50,
    duration: float = 120.0,
    tokens: int = 500,
    exit_code: int = 0,
    halt_reason: str | None = None,
    final_state: str = "Done",
) -> dict:
    return {
        "issue_identifier": issue,
        "started_at": "2026-05-22T10:00:00+00:00",
        "finished_at": "2026-05-22T10:02:00+00:00",
        "exit_code": exit_code,
        "final_linear_state": final_state,
        "worktree_path": f"/tmp/.worktrees/{issue}",
        "halt_reason": halt_reason,
        "duration_seconds": duration,
        "model": None,
        "usage": {"cumulative": tokens},
        "cost_usd": cost,
        "outcome_verdict": outcome,
        "review_verdict": review,
        "prep_verdict": None,
    }


def _summary_line(out: str, label: str) -> str:
    line = next((ln for ln in out.splitlines() if label in ln), None)
    assert line is not None, f"no SUMMARY line for {label!r}"
    return line


def test_avg_cost_per_cycle_and_per_issue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two cycles, 4 issues, $2.00 total → $1.00/cycle, $0.50/issue."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-A",
        [_entry("ABA-1"), _entry("ABA-2")],
        "cycle-A-20260522T100000000000Z.json",
    )
    _write_run(
        runs_dir, "cycle-B",
        [_entry("ABA-3"), _entry("ABA-4")],
        "cycle-B-20260522T110000000000Z.json",
    )

    scorecard.run(runs_dir)

    line = _summary_line(capsys.readouterr().out, "Avg cost")
    assert "$1.00 / cycle" in line
    assert "$0.50 / issue" in line


def test_avg_duration_per_cycle_and_per_issue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Wall-clock comes from cycle_duration_seconds: 600s + 600s over 2 cycles,
    4 issues → 10m/cycle, 5m/issue."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-A",
        [_entry("ABA-1"), _entry("ABA-2")],
        "cycle-A-20260522T100000000000Z.json",
        cycle_duration_seconds=600.0,
    )
    _write_run(
        runs_dir, "cycle-B",
        [_entry("ABA-3"), _entry("ABA-4")],
        "cycle-B-20260522T110000000000Z.json",
        cycle_duration_seconds=600.0,
    )

    scorecard.run(runs_dir)

    line = _summary_line(capsys.readouterr().out, "Avg duration")
    assert "10m / cycle" in line
    assert "5m / issue" in line


def test_duration_falls_back_to_entry_sum_for_legacy_logs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When a payload omits cycle_duration_seconds, sum the entry durations."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-A",
        [_entry("ABA-1", duration=120.0), _entry("ABA-2", duration=120.0)],
        "cycle-A-20260522T100000000000Z.json",
        cycle_duration_seconds=None,
    )

    scorecard.run(runs_dir)

    # 240s total over 1 cycle = 4m; over 2 issues = 2m.
    line = _summary_line(capsys.readouterr().out, "Avg duration")
    assert "4m / cycle" in line
    assert "2m / issue" in line


def test_fully_executed_excludes_halted_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A halted run does not count as fully executed: 1 clean + 1 halted → 1/2."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-A",
        [
            _entry("ABA-1"),
            _entry(
                "ABA-2", outcome=None, review=None,
                halt_reason="token cap", final_state="In Progress",
            ),
        ],
        "cycle-A-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    line = _summary_line(capsys.readouterr().out, "Fully executed")
    assert "1/2 (50%)" in line


def test_fully_executed_excludes_nonzero_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A nonzero exit_code is not fully executed even without a halt_reason."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-A",
        [
            _entry("ABA-1"),
            _entry("ABA-2", exit_code=1),
        ],
        "cycle-A-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    line = _summary_line(capsys.readouterr().out, "Fully executed")
    assert "1/2 (50%)" in line


def test_retry_rate_counts_retried_issue_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One issue with two attempts and one single-attempt issue → 1/2 retried."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-A",
        [
            _entry("ABA-1"),
            _entry("ABA-1"),
            _entry("ABA-2"),
        ],
        "cycle-A-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    line = _summary_line(capsys.readouterr().out, "Retry rate")
    # 2 distinct issues, 1 retried.
    assert "1/2 (50%)" in line


def test_cost_per_correct_issue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """$2.00 spent across 4 issues, 3 correct → $0.67 per correct issue."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-A",
        [
            _entry("ABA-1"),
            _entry("ABA-2"),
            _entry("ABA-3"),
            _entry("ABA-4", outcome={"result": "fail", "findings": []}),
        ],
        "cycle-A-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    line = _summary_line(capsys.readouterr().out, "Cost / correct")
    assert "$0.67" in line


def test_cost_per_correct_is_na_with_no_correct(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-A",
        [_entry("ABA-1", outcome={"result": "fail", "findings": []})],
        "cycle-A-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    line = _summary_line(capsys.readouterr().out, "Cost / correct")
    assert "n/a" in line


def test_per_cycle_summary_row_carries_rolled_up_stats(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The cycle's own row shows issues, executed, pass, cost, and duration."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-Z",
        [
            _entry("ABA-1"),
            _entry("ABA-2", outcome={"result": "fail", "findings": []}),
        ],
        "cycle-Z-20260522T100000000000Z.json",
        cycle_duration_seconds=600.0,
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    row = next((ln for ln in out.splitlines() if "cycle-Z" in ln), None)
    assert row is not None
    assert "2/2" in row          # executed
    assert "1/2 (50%)" in row    # pass
    assert "$1.00" in row        # cost
    assert "10m" in row          # duration


def test_avg_rows_show_na_when_a_cycle_has_no_issues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A cycle with an empty entries list still prints labeled Avg rows (n/a),
    not a SUMMARY with the rows silently dropped."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-empty",
        [],
        "cycle-empty-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    # One cycle (so per-cycle resolves), but zero issues → per-issue is n/a.
    assert "n/a / issue" in _summary_line(out, "Avg cost")
    assert "n/a / issue" in _summary_line(out, "Avg duration")


def test_summary_header_counts_cycles_and_issues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-A",
        [_entry("ABA-1"), _entry("ABA-2")],
        "cycle-A-20260522T100000000000Z.json",
    )
    _write_run(
        runs_dir, "cycle-B",
        [_entry("ABA-3")],
        "cycle-B-20260522T110000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    assert "2 cycles · 3 issues" in out
