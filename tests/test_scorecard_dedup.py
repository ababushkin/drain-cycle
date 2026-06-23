"""Per-issue deduplication tests for ``drain-cycle scorecard``.

Verifies that the pass-rate denominator counts each issue_identifier once per
cycle, using the latest (chronological) attempt as the scored result. The
per-attempt detail rows must remain visible regardless.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drain_cycle import scorecard


_PASS_VERDICT = {"result": "pass", "findings": [], "invoked_at": "2026-05-22T10:05:00+00:00"}
_FAIL_VERDICT = {"result": "fail", "findings": ["not met"], "invoked_at": "2026-05-22T10:05:00+00:00"}
_GO_VERDICT = {"result": "go", "findings": [], "invocation_id": "abc"}


def _write_run(
    runs_dir: Path,
    cycle_id: str,
    entries: list[dict],
    filename: str,
) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "cycle_id": cycle_id,
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


def test_pass_rate_counts_issue_once_when_retried(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An issue with two attempts counts once in the pass-rate denominator."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-1",
        [
            _base_entry("ABA-1", _FAIL_VERDICT, _GO_VERDICT),
            _base_entry("ABA-1", _PASS_VERDICT, _GO_VERDICT),
        ],
        "cycle-1-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    # 1 issue, latest attempt correct → 1/1 (100%)
    assert "1/1" in out


def test_latest_attempt_used_for_correctness(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The last attempt in the entries list determines the issue's verdict."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-1",
        [
            _base_entry("ABA-1", _PASS_VERDICT, _GO_VERDICT),
            _base_entry("ABA-1", _FAIL_VERDICT, _GO_VERDICT),
        ],
        "cycle-1-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    # latest attempt failed → 0/1 (0%)
    assert "0/1" in out


def test_all_attempt_rows_appear_in_detail_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """All per-attempt rows stay visible even when an issue retried."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-1",
        [
            _base_entry("ABA-1", _FAIL_VERDICT, _GO_VERDICT, duration=10.0),
            _base_entry("ABA-1", _PASS_VERDICT, _GO_VERDICT, duration=20.0),
        ],
        "cycle-1-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir, detail=True)

    out = capsys.readouterr().out
    # Both rows appear in the detail table, identified by their distinct durations.
    assert "10.0" in out
    assert "20.0" in out


def test_multi_issue_cycle_counts_each_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two distinct issues, one retried: pass-rate denominator is 2, not 3."""
    runs_dir = tmp_path / "runs"
    _write_run(
        runs_dir,
        "cycle-1",
        [
            _base_entry("ABA-1", _FAIL_VERDICT, _GO_VERDICT),
            _base_entry("ABA-1", _PASS_VERDICT, _GO_VERDICT),
            _base_entry("ABA-2", _FAIL_VERDICT, _GO_VERDICT),
        ],
        "cycle-1-20260522T100000000000Z.json",
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    # ABA-1 (latest: pass) + ABA-2 (fail) → 1/2 (50%)
    assert "1/2" in out
