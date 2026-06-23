"""Dashboard-rendering tests for ``drain-cycle scorecard``.

Covers the presentation layer added on top of the correctness rule: the
sparkline, the status glyph (including the dormant UNSCORED state), and the
headline KPI block.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drain_cycle import scorecard
from drain_cycle.scorecard import Status, _sparkline, _status, _status_glyph

_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"
_PASS_VERDICT = {"result": "pass", "findings": [], "invoked_at": "2026-05-22T10:05:00+00:00"}
_GO_VERDICT = {"result": "go", "findings": [], "invocation_id": "abc"}


def test_sparkline_empty_is_blank() -> None:
    assert _sparkline([]) == ""


def test_sparkline_endpoints_map_to_low_and_high_blocks() -> None:
    assert _sparkline([0.0]) == "▁"
    assert _sparkline([1.0]) == "█"


def test_sparkline_length_and_alphabet() -> None:
    spark = _sparkline([0.1, 0.5, 0.9])
    assert len(spark) == 3
    assert all(ch in _SPARK_BLOCKS for ch in spark)


def test_status_glyph_maps_all_three_states() -> None:
    assert "✓" in _status_glyph(Status.CORRECT)
    assert "✗" in _status_glyph(Status.FAILED)
    assert "—" in _status_glyph(Status.UNSCORED)


def test_null_verdict_currently_classifies_as_failed() -> None:
    """Interim behavior: with no verdict, a run renders FAILED, not UNSCORED."""
    entry = {"outcome_verdict": None, "review_verdict": None}
    assert _status(entry) is Status.FAILED


def test_kpi_header_renders(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "issue_identifier": "ABA-1",
        "final_linear_state": "Done",
        "duration_seconds": 120.0,
        "cost_usd": 0.005,
        "usage": {"cumulative": 500},
        "outcome_verdict": _PASS_VERDICT,
        "review_verdict": _GO_VERDICT,
        "prep_verdict": None,
    }
    (runs_dir / "cycle-1-20260522T100000000000Z.json").write_text(
        json.dumps({"cycle_id": "cycle-1", "entries": [entry]}) + "\n"
    )

    scorecard.run(runs_dir)

    out = capsys.readouterr().out
    assert "drain-cycle scorecard" in out
    assert "Pass rate" in out
    assert "%" in out
    assert "scored" in out
