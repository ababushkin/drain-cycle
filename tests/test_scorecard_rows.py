"""Row-output tests for ``drain-cycle scorecard``.

Verifies that scorecard.run() prints one row per entry per cycle, grouped
under a cycle header, with issue ID, duration, cost, tokens, and a
correctness indicator.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drain_cycle import scorecard


_PASS_VERDICT = {"result": "pass", "findings": [], "invoked_at": "2026-05-22T10:05:00+00:00"}
_FAIL_VERDICT = {"result": "fail", "findings": ["not met"], "invoked_at": "2026-05-22T10:05:00+00:00"}
_GO_VERDICT = {"result": "go", "findings": [], "invocation_id": "abc"}
_NOGO_VERDICT = {"result": "no-go", "findings": ["review failed"], "invocation_id": "def"}


def _write_run(
    runs_dir: Path,
    cycle_id: str,
    entries: list[dict],
    filename: str,
) -> None:
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


def _base_entry(
    issue: str,
    outcome: dict | None,
    review: dict | None,
    duration: float = 120.0,
    cost: float = 0.005,
    tokens: int = 500,
) -> dict:
    return {
        "issue_identifier": issue,
        "started_at": "2026-05-22T10:00:00+00:00",
        "finished_at": "2026-05-22T10:02:00+00:00",
        "exit_code": 0,
        "final_linear_state": "Done",
        "worktree_path": f"/tmp/.worktrees/{issue}",
        "halt_reason": None,
        "duration_seconds": duration,
        "model": None,
        "usage": {"cumulative": tokens, "peak_context": 400,
                  "input_tokens": 300, "output_tokens": 200,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        "cost_usd": cost,
        "num_turns": None,
        "session_id": None,
        "is_error": None,
        "outcome_verdict": outcome,
        "review_verdict": review,
        "prep_verdict": None,
        "responder_runs": [],
        "finishing_runs": [],
    }


def test_each_entry_gets_one_row(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-1",
        [
            _base_entry("ABA-1", _PASS_VERDICT, _GO_VERDICT),
            _base_entry("ABA-2", _FAIL_VERDICT, _GO_VERDICT),
        ],
        "cycle-1-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    assert "ABA-1" in out
    assert "ABA-2" in out


def test_cycle_header_appears(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-abc",
        [_base_entry("ABA-1", _PASS_VERDICT, _GO_VERDICT)],
        "cycle-abc-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    assert "cycle-abc" in out


def test_duration_shown_in_row(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-1",
        [_base_entry("ABA-1", _PASS_VERDICT, _GO_VERDICT, duration=95.3)],
        "cycle-1-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    assert "95.3" in out


def test_cost_shown_in_row(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-1",
        [_base_entry("ABA-1", _PASS_VERDICT, _GO_VERDICT, cost=0.0123)],
        "cycle-1-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    assert "0.0123" in out


def test_tokens_shown_in_row(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-1",
        [_base_entry("ABA-1", _PASS_VERDICT, _GO_VERDICT, tokens=12345)],
        "cycle-1-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    assert "12k" in out


def test_correct_entry_shows_correct_glyph(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-1",
        [_base_entry("ABA-1", _PASS_VERDICT, _GO_VERDICT)],
        "cycle-1-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    assert "✓" in out
    assert "✗" not in out


def test_fail_outcome_shows_failed_glyph(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-1",
        [_base_entry("ABA-1", _FAIL_VERDICT, _GO_VERDICT)],
        "cycle-1-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    assert "✗" in out


def test_nogo_review_shows_failed_glyph(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-1",
        [_base_entry("ABA-1", _PASS_VERDICT, _NOGO_VERDICT)],
        "cycle-1-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    assert "✗" in out


def test_missing_review_shows_failed_glyph(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-1",
        [_base_entry("ABA-1", _PASS_VERDICT, None)],
        "cycle-1-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    assert "✗" in out


def test_entries_from_same_cycle_appear_together(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two files sharing cycle_id → entries grouped under one header."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-shared",
        [_base_entry("ABA-1", _PASS_VERDICT, _GO_VERDICT)],
        "cycle-shared-20260522T100000000000Z.json",
    )
    _write_run(
        runs_dir,
        "cycle-shared",
        [_base_entry("ABA-2", _PASS_VERDICT, _GO_VERDICT)],
        "cycle-shared-20260522T110000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    # Only one header for the cycle
    assert out.count("cycle-shared") == 1
    assert "ABA-1" in out
    assert "ABA-2" in out


def test_distinct_cycles_each_get_a_header(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-A",
        [_base_entry("ABA-1", _PASS_VERDICT, _GO_VERDICT)],
        "cycle-A-20260522T100000000000Z.json",
    )
    _write_run(
        runs_dir,
        "cycle-B",
        [_base_entry("ABA-2", _PASS_VERDICT, _GO_VERDICT)],
        "cycle-B-20260522T110000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    assert "cycle-A" in out
    assert "cycle-B" in out
