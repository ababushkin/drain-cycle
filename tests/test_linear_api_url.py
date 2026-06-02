"""Tests confirming the LINEAR_API_URL override seam in linear._post.

The seam lets integration tests redirect GraphQL calls to a local MockLinear
server without touching production code beyond reading an env var.
"""
from __future__ import annotations

import pytest
import httpx

from drain_cycle import linear


class _FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"data": {}}


def test_post_uses_linear_api_url_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_URL", "http://localhost:9999/graphql")
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")

    captured: list[str] = []

    def fake_http_post(url: str, **_kwargs: object) -> _FakeResponse:
        captured.append(url)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_http_post)
    linear._post("{ __typename }", operation="test")

    assert captured == ["http://localhost:9999/graphql"]


def test_post_uses_default_url_when_env_var_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINEAR_API_URL", raising=False)
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")

    captured: list[str] = []

    def fake_http_post(url: str, **_kwargs: object) -> _FakeResponse:
        captured.append(url)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_http_post)
    linear._post("{ __typename }", operation="test")

    assert captured == [linear._DEFAULT_GRAPHQL_URL]
