"""Tests for ``linear.resolve_project_id`` error paths.

Covers:
- Zero matches → RuntimeError naming the project.
- Multiple matches → RuntimeError listing all conflicting names.
"""
from __future__ import annotations

import pytest

from drain_cycle import linear


def test_resolve_project_id_raises_for_zero_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(q: str, v: dict | None = None, *, operation: str = "graphql") -> dict:
        return {"projects": {"nodes": []}}

    monkeypatch.setattr(linear, "_post", fake_post)
    with pytest.raises(RuntimeError, match="not found"):
        linear.resolve_project_id("No Such Project")


def test_resolve_project_id_raises_for_multiple_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(q: str, v: dict | None = None, *, operation: str = "graphql") -> dict:
        return {
            "projects": {
                "nodes": [
                    {"id": "id-1", "name": "Drain Alpha"},
                    {"id": "id-2", "name": "Drain Beta"},
                ]
            }
        }

    monkeypatch.setattr(linear, "_post", fake_post)
    with pytest.raises(RuntimeError, match="ambiguous") as exc_info:
        linear.resolve_project_id("Drain")
    msg = str(exc_info.value)
    assert "Drain Alpha" in msg
    assert "Drain Beta" in msg
