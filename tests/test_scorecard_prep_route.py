"""Prep-route advisory column tests for ``drain-cycle scorecard``.

Pins that prep_verdict.route appears in output but never changes the
correctness rate or exit code.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drain_cycle import scorecard


_PASS_VERDICT = {"result": "pass", "findings": [], "invoked_at": "2026-05-22T10:05:00+00:00"}
_GO_VERDICT = {"result": "GO", "findings": [], "invocation_id": "abc"}
_AUTO_MERGE_PREP = {"result": "ok", "route": "auto-merge", "reasoning": "clean"}
_HUMAN_REVIEW_PREP = {"result": "needs-review", "route": "human-review", "reasoning": "risk"}


def _write_run(runs_dir: Path, cycle_id: str, entries: list[dict], filename: str) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / filename).write_text(
        json.dumps(
            {
                "cycle_id": cycle_id,
                "cycle_duration_seconds": 60.0,
                "cycle_cost_usd": 0.01,
                "cycle_tokens_cumulative": 1000,
                "cycle_halt_reason": None,
                "entries": entries,
            }
        )
        + "\n"
    )


def _entry(issue: str, prep: dict | None) -> dict:
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
        "outcome_verdict": _PASS_VERDICT,
        "review_verdict": _GO_VERDICT,
        "prep_verdict": prep,
        "responder_runs": [],
        "finishing_runs": [],
    }


def test_auto_merge_route_appears_in_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-1",
        [_entry("ABA-1", _AUTO_MERGE_PREP)],
        "cycle-1-20260522T100000000000Z.json",
    )
    scorecard.run(runs_dir)
    out = capsys.readouterr().out
    assert "auto-merge" in out


def test_human_review_route_appears_in_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-1",
        [_entry("ABA-1", _HUMAN_REVIEW_PREP)],
        "cycle-1-20260522T100000000000Z.json",
    )
    scorecard.run(runs_dir)
    out = capsys.readouterr().out
    assert "human-review" in out


def test_prep_route_does_not_change_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A human-review route on an otherwise clean run must not cause exit 1."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-1",
        [_entry("ABA-1", _HUMAN_REVIEW_PREP)],
        "cycle-1-20260522T100000000000Z.json",
    )
    exit_code = scorecard.run(runs_dir)
    assert exit_code == 0


def test_prep_route_does_not_change_pass_rate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """human-review route on a correct run: rate stays 1/1 (100%), not 0/1."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-1",
        [_entry("ABA-1", _HUMAN_REVIEW_PREP)],
        "cycle-1-20260522T100000000000Z.json",
    )
    scorecard.run(runs_dir)
    out = capsys.readouterr().out
    assert "1/1 (100%)" in out


def test_null_prep_verdict_shows_no_route(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir, "cycle-1",
        [_entry("ABA-1", None)],
        "cycle-1-20260522T100000000000Z.json",
    )
    scorecard.run(runs_dir)
    out = capsys.readouterr().out
    assert "auto-merge" not in out
    assert "human-review" not in out
