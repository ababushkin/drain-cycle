"""Tests for ``drain_cycle.flow``.

Flow resolution is simple: ``"verify"`` label present → ``"verify"``;
absent → ``None``. Never raises.
"""
from __future__ import annotations

from drain_cycle import flow


def test_no_labels_returns_none() -> None:
    assert flow.resolve({"identifier": "ABA-1", "labels": []}) is None


def test_missing_labels_key_returns_none() -> None:
    assert flow.resolve({"identifier": "ABA-1"}) is None


def test_verify_label_returns_verify() -> None:
    issue = {"identifier": "ABA-1", "labels": ["verify"]}
    assert flow.resolve(issue) == "verify"


def test_verify_label_alongside_repo_and_model_returns_verify() -> None:
    issue = {"identifier": "ABA-1", "labels": ["repo:alpha", "model:sonnet", "verify"]}
    assert flow.resolve(issue) == "verify"


def test_other_labels_only_returns_none() -> None:
    issue = {"identifier": "ABA-1", "labels": ["repo:alpha", "model:opus"]}
    assert flow.resolve(issue) is None
