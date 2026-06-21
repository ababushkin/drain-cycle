"""Tests for ``drain_cycle.kr2_check``.

The KR2 schema check must exit 0 when every Done entry carries outcome_verdict,
and exit 1 when any Done entry is missing it. Non-Done entries with null
verdicts are always compliant (halt_reason explains the missing assessment).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drain_cycle.kr2_check import check_file, main


def _entry(
    identifier: str,
    *,
    state: str = "Done",
    outcome_verdict: object = None,
    halt_reason: object = None,
) -> dict:
    return {
        "issue_identifier": identifier,
        "final_linear_state": state,
        "outcome_verdict": outcome_verdict,
        "halt_reason": halt_reason,
    }


def _write_log(path: Path, entries: list[dict]) -> Path:
    log_file = path / "run.json"
    log_file.write_text(json.dumps({"cycle_id": "c1", "entries": entries}))
    return log_file


_OV = {"result": "pass", "findings": [], "invoked_at": "2026-01-01T00:00:00Z"}


def test_compliant_log_exits_zero(tmp_path: Path) -> None:
    log = _write_log(tmp_path, [_entry("ABA-1", outcome_verdict=_OV)])
    assert main([str(log)]) == 0


def test_done_entry_missing_outcome_verdict_exits_one(tmp_path: Path) -> None:
    log = _write_log(tmp_path, [_entry("ABA-1")])
    assert main([str(log)]) == 1


def test_halted_entry_without_verdicts_is_compliant(tmp_path: Path) -> None:
    log = _write_log(
        tmp_path,
        [_entry("ABA-1", state="Todo", halt_reason="Halt: ABA-1 left Todo")],
    )
    assert main([str(log)]) == 0


def test_mixed_log_exits_one_for_bad_done_entry(tmp_path: Path) -> None:
    log = _write_log(
        tmp_path,
        [
            _entry("ABA-1", outcome_verdict=_OV),
            _entry("ABA-2"),  # Done, missing outcome_verdict
        ],
    )
    assert main([str(log)]) == 1


def test_empty_entries_exits_zero(tmp_path: Path) -> None:
    log = _write_log(tmp_path, [])
    assert main([str(log)]) == 0


def test_no_args_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2


def test_unreadable_file_exits_one(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.json"
    assert main([str(missing)]) == 1


def test_check_file_returns_violations(tmp_path: Path) -> None:
    log = _write_log(tmp_path, [_entry("ABA-1")])
    violations = check_file(log)
    assert len(violations) == 1
    assert any("outcome_verdict" in v for v in violations)
