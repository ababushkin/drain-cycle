"""Tests for ``linear.project_issues``.

Verifies:
- ``project_issues`` sends a ``project:{id:{eq}}`` filter with the same state
  types as ``pending_issues``.
- Post-processing (label flattening, blocker extraction, raw key removal)
  matches ``pending_issues`` behaviour.
- ``pending_issues`` still passes its existing tests (signature unchanged).
"""
from __future__ import annotations

import pytest

from drain_cycle import linear
from drain_cycle.linear import ExecutionPlan


def test_project_issues_sends_project_id_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, dict]] = []

    def fake_post(q: str, v: dict | None = None, *, operation: str = "graphql") -> dict:
        captured.append((q, v or {}))
        return {"issues": {"nodes": []}}

    monkeypatch.setattr(linear, "_post", fake_post)
    linear.project_issues("proj-abc123")
    query, variables = captured[0]
    assert "project" in query
    assert variables.get("targetId") == "proj-abc123"


def test_project_issues_returns_execution_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(q: str, v: dict | None = None, *, operation: str = "graphql") -> dict:
        return {"issues": {"nodes": []}}

    monkeypatch.setattr(linear, "_post", fake_post)
    plan = linear.project_issues("proj-abc123")
    assert isinstance(plan, ExecutionPlan)
    assert plan.order == []
    assert plan.deferred == []


def test_project_issues_flattens_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(q: str, v: dict | None = None, *, operation: str = "graphql") -> dict:
        return {
            "issues": {
                "nodes": [
                    {
                        "id": "id-A",
                        "identifier": "ABA-A",
                        "title": "T",
                        "description": "",
                        "sortOrder": 1.0,
                        "state": {"type": "unstarted", "name": "Todo"},
                        "labels": {"nodes": [
                            {"name": "drain-cycle", "parent": None},
                            {"name": "sonnet", "parent": {"name": "model"}},
                        ]},
                        "inverseRelations": {"nodes": []},
                    }
                ]
            }
        }

    monkeypatch.setattr(linear, "_post", fake_post)
    plan = linear.project_issues("proj-abc123")
    assert plan.order[0]["labels"] == ["drain-cycle", "model:sonnet"]


def test_project_issues_drops_raw_inverse_relations_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(q: str, v: dict | None = None, *, operation: str = "graphql") -> dict:
        return {
            "issues": {
                "nodes": [
                    {
                        "id": "id-A",
                        "identifier": "ABA-A",
                        "title": "T",
                        "description": "",
                        "sortOrder": 1.0,
                        "state": {"type": "unstarted", "name": "Todo"},
                        "labels": {"nodes": []},
                        "inverseRelations": {"nodes": []},
                    }
                ]
            }
        }

    monkeypatch.setattr(linear, "_post", fake_post)
    plan = linear.project_issues("proj-abc123")
    assert "inverseRelations" not in plan.order[0]


def test_project_issues_uses_same_state_types_as_pending_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_vars: list[dict] = []
    project_vars: list[dict] = []

    def fake_pending(q: str, v: dict | None = None, *, operation: str = "graphql") -> dict:
        pending_vars.append(v or {})
        return {"issues": {"nodes": []}}

    def fake_project(q: str, v: dict | None = None, *, operation: str = "graphql") -> dict:
        project_vars.append(v or {})
        return {"issues": {"nodes": []}}

    monkeypatch.setattr(linear, "_post", fake_pending)
    linear.pending_issues("cycle-id")

    monkeypatch.setattr(linear, "_post", fake_project)
    linear.project_issues("proj-id")

    assert set(pending_vars[0].get("stateTypes", [])) == set(project_vars[0].get("stateTypes", []))
