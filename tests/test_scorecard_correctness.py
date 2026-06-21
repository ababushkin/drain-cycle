"""Correctness function tests for ``drain-cycle scorecard``.

Pins the correctness rule from ADR 0031: a run is correct when
outcome_verdict.result == "pass" AND review_verdict.result == "go".
Each case is independent — the function reads entry dicts directly.
"""
from __future__ import annotations

from typing import Any

import pytest

from drain_cycle.scorecard import _is_correct, _is_silent_done


_PASS_VERDICT = {"result": "pass", "findings": [], "invoked_at": "2026-05-22T10:05:00+00:00"}
_FAIL_VERDICT = {"result": "fail", "findings": ["not met"], "invoked_at": "2026-05-22T10:05:00+00:00"}
_GO_VERDICT = {"result": "go", "findings": [], "invocation_id": "abc"}
_NOGO_VERDICT = {"result": "no-go", "findings": ["review failed"], "invocation_id": "def"}


def _entry(
    outcome: dict[str, Any] | None,
    review: dict[str, Any] | None,
    final_state: str = "Done",
) -> dict[str, Any]:
    return {
        "issue_identifier": "ABA-1",
        "final_linear_state": final_state,
        "outcome_verdict": outcome,
        "review_verdict": review,
    }


# --- correctness rule ---

def test_outcome_pass_and_review_go_is_correct() -> None:
    assert _is_correct(_entry(_PASS_VERDICT, _GO_VERDICT)) is True


def test_outcome_fail_and_review_go_is_not_correct() -> None:
    assert _is_correct(_entry(_FAIL_VERDICT, _GO_VERDICT)) is False


def test_outcome_pass_and_review_nogo_is_not_correct() -> None:
    assert _is_correct(_entry(_PASS_VERDICT, _NOGO_VERDICT)) is False


def test_outcome_fail_and_review_nogo_is_not_correct() -> None:
    assert _is_correct(_entry(_FAIL_VERDICT, _NOGO_VERDICT)) is False


def test_outcome_pass_and_missing_review_is_not_correct() -> None:
    """Missing review verdict is not-correct (advisory only — not a violation)."""
    assert _is_correct(_entry(_PASS_VERDICT, None)) is False


def test_outcome_fail_and_missing_review_is_not_correct() -> None:
    assert _is_correct(_entry(_FAIL_VERDICT, None)) is False


def test_null_outcome_is_not_correct() -> None:
    """A null outcome_verdict can never be correct."""
    assert _is_correct(_entry(None, _GO_VERDICT)) is False


def test_both_null_is_not_correct() -> None:
    assert _is_correct(_entry(None, None)) is False


# --- silent-Done rule (separate from correctness) ---

def test_done_with_null_outcome_is_silent_done() -> None:
    """Done + null outcome_verdict is the hard violation."""
    assert _is_silent_done(_entry(None, None, final_state="Done")) is True


def test_done_with_pass_outcome_is_not_silent_done() -> None:
    assert _is_silent_done(_entry(_PASS_VERDICT, _GO_VERDICT, final_state="Done")) is False


def test_done_with_fail_outcome_is_not_silent_done() -> None:
    """A fail verdict is not silent — the verifier ran."""
    assert _is_silent_done(_entry(_FAIL_VERDICT, _GO_VERDICT, final_state="Done")) is False


def test_not_done_with_null_outcome_is_not_silent_done() -> None:
    """Only Done entries trigger the silent-Done violation."""
    assert _is_silent_done(_entry(None, None, final_state="In Progress")) is False
