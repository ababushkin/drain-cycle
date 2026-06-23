"""Exit-code and summary-format tests for ``drain-cycle scorecard``.

Pins: Done+null_outcome→exit 1 and listed; clean run→exit 0;
per-cycle N/D (P%) and overall N/D (P%) printed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drain_cycle import scorecard


_PASS_VERDICT = {"result": "pass", "findings": [], "invoked_at": "2026-05-22T10:05:00+00:00"}
_FAIL_VERDICT = {"result": "fail", "findings": ["not met"], "invoked_at": "2026-05-22T10:05:00+00:00"}
_GO_VERDICT = {"result": "go", "findings": [], "invocation_id": "abc"}


def _write_run(runs_dir: Path, cycle_id: str, entries: list[dict], filename: str) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "cycle_id": cycle_id,
        "cycle_duration_seconds": 60.0,
        "cycle_cost_usd": 0.01,
        "cycle_tokens_cumulative": 1000,
        "cycle_halt_reason": None,
        "entries": entries,
    }
    (runs_dir / filename).write_text(json.dumps(payload) + "\n")


def _done_entry(issue: str, outcome: dict | None, review: dict | None = _GO_VERDICT) -> dict:
    return {
        "issue_identifier": issue,
        "started_at": "2026-05-22T10:00:00+00:00",
        "finished_at": "2026-05-22T10:02:00+00:00",
        "exit_code": 0,
        "final_linear_state": "Done",
        "worktree_path": f"/tmp/.worktrees/{issue}",
        "halt_reason": None,
        "duration_seconds": 120.0,
        "model": None,
        "usage": {"cumulative": 500, "peak_context": 400,
                  "input_tokens": 300, "output_tokens": 200,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        "cost_usd": 0.005,
        "num_turns": None,
        "session_id": None,
        "is_error": None,
        "outcome_verdict": outcome,
        "review_verdict": review,
        "prep_verdict": None,
        "responder_runs": [],
        "finishing_runs": [],
    }


# --- exit code ---

def test_null_outcome_done_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-1",
        [_done_entry("ABA-1", None)],
        "cycle-1-20260522T100000000000Z.json",
    )
    assert scorecard.run(runs_dir) != 0


def test_clean_run_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-1",
        [_done_entry("ABA-1", _PASS_VERDICT)],
        "cycle-1-20260522T100000000000Z.json",
    )
    assert scorecard.run(runs_dir) == 0


def test_fail_outcome_not_a_violation_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fail verdict is not silent-Done — the verifier ran. Exit 0."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-1",
        [_done_entry("ABA-1", _FAIL_VERDICT)],
        "cycle-1-20260522T100000000000Z.json",
    )
    assert scorecard.run(runs_dir) == 0


def test_empty_runs_dir_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    assert scorecard.run(runs_dir) == 0


def test_silent_done_issue_listed_in_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-1",
        [_done_entry("ABA-99", None)],
        "cycle-1-20260522T100000000000Z.json",
    )
    scorecard.run(runs_dir)
    out = capsys.readouterr().out
    assert "ABA-99" in out
    assert "silent-Done" in out


def test_clean_run_shows_no_violations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-1",
        [_done_entry("ABA-1", _PASS_VERDICT)],
        "cycle-1-20260522T100000000000Z.json",
    )
    scorecard.run(runs_dir)
    out = capsys.readouterr().out
    assert "silent-Done violations: none" in out


# --- summary format N/D (P%) ---

def test_overall_pass_rate_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """2 correct out of 3 → 2/3 (67%)."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-1",
        [
            _done_entry("ABA-1", _PASS_VERDICT),
            _done_entry("ABA-2", _PASS_VERDICT),
            _done_entry("ABA-3", _FAIL_VERDICT),
        ],
        "cycle-1-20260522T100000000000Z.json",
    )
    scorecard.run(runs_dir)
    out = capsys.readouterr().out
    assert "2/3 (67%)" in out


def test_overall_pass_rate_100_percent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-1",
        [
            _done_entry("ABA-1", _PASS_VERDICT),
            _done_entry("ABA-2", _PASS_VERDICT),
        ],
        "cycle-1-20260522T100000000000Z.json",
    )
    scorecard.run(runs_dir)
    out = capsys.readouterr().out
    assert "2/2 (100%)" in out


def test_per_cycle_pass_rate_shown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-X",
        [
            _done_entry("ABA-1", _PASS_VERDICT),
            _done_entry("ABA-2", _FAIL_VERDICT),
        ],
        "cycle-X-20260522T100000000000Z.json",
    )
    scorecard.run(runs_dir)
    out = capsys.readouterr().out
    # the cycle's own header line carries its pass-rate fraction
    lines = out.splitlines()
    cycle_rate_line = next((l for l in lines if "cycle-X" in l), None)
    assert cycle_rate_line is not None
    assert "1/2 (50%)" in cycle_rate_line


def test_multi_cycle_overall_aggregates_all(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cycle A: 1/1 correct. Cycle B: 1/2 correct. Overall: 2/3 (67%)."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-A",
        [_done_entry("ABA-1", _PASS_VERDICT)],
        "cycle-A-20260522T100000000000Z.json",
    )
    _write_run(
        runs_dir, "cycle-B",
        [
            _done_entry("ABA-2", _PASS_VERDICT),
            _done_entry("ABA-3", _FAIL_VERDICT),
        ],
        "cycle-B-20260522T110000000000Z.json",
    )
    scorecard.run(runs_dir)
    out = capsys.readouterr().out
    assert "2/3 (67%)" in out


def test_fail_verdict_not_counted_correct(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: a FAIL outcome_verdict must show 0/1 correct, not 1/1.

    The pre-scorecard grading bug never read outcome_verdict.result and
    counted every Done entry as passing. The scorecard must not repeat this.
    """
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-1",
        [_done_entry("ABA-1", _FAIL_VERDICT)],
        "cycle-1-20260522T100000000000Z.json",
    )
    scorecard.run(runs_dir)
    out = capsys.readouterr().out
    assert "0/1 (0%)" in out
    assert "✗" in out
