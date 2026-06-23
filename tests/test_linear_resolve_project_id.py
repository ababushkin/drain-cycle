"""Tests for ``linear.resolve_project_id``.

Covers:
- UUID pass-through: a well-formed UUID is returned unchanged with no API call.
- Name resolution: a name triggers a ``projects(filter:{name:{eq}})`` query and
  the first match's id is returned.
"""
from __future__ import annotations

import pytest

from drain_cycle import linear


def test_resolve_project_id_passes_uuid_through(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def fake_post(q: str, v: dict | None = None, *, operation: str = "graphql") -> dict:
        called.append(True)
        return {}

    monkeypatch.setattr(linear, "_post", fake_post)
    uuid = "a0b1c2d3-e4f5-6789-abcd-ef0123456789"
    assert linear.resolve_project_id(uuid) == uuid
    assert called == []


def test_resolve_project_id_resolves_name_to_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(q: str, v: dict | None = None, *, operation: str = "graphql") -> dict:
        return {"projects": {"nodes": [{"id": "proj-abc123", "name": "My Project"}]}}

    monkeypatch.setattr(linear, "_post", fake_post)
    assert linear.resolve_project_id("My Project") == "proj-abc123"


def test_resolve_project_id_sends_name_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, dict]] = []

    def fake_post(q: str, v: dict | None = None, *, operation: str = "graphql") -> dict:
        captured.append((q, v or {}))
        return {"projects": {"nodes": [{"id": "proj-abc123", "name": "My Project"}]}}

    monkeypatch.setattr(linear, "_post", fake_post)
    linear.resolve_project_id("My Project")
    query, variables = captured[0]
    assert "projects" in query
    assert "name" in query
    assert variables.get("name") == "My Project"
