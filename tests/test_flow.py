"""Unit tests for per-issue execution-flow resolution."""
from __future__ import annotations

from drain_cycle import flow


def test_no_labels_returns_none() -> None:
    assert flow.resolve({"labels": []}) is None


def test_missing_labels_key_returns_none() -> None:
    assert flow.resolve({}) is None


def test_verify_label_returns_verify() -> None:
    assert flow.resolve({"labels": ["verify"]}) == flow.VERIFY


def test_verify_label_among_others_returns_verify() -> None:
    assert flow.resolve({"labels": ["repo:drain-cycle", "verify", "sonnet"]}) == flow.VERIFY


def test_unrelated_labels_return_none() -> None:
    assert flow.resolve({"labels": ["repo:drain-cycle", "sonnet", "source-driven-development"]}) is None


def test_verify_constant_is_string() -> None:
    assert isinstance(flow.VERIFY, str)
    assert flow.VERIFY == "verify"
