"""Tests for ``drain_cycle.handoff``.

``read`` must never raise: a missing file, a truncated JSON write, or a
structurally wrong payload all return ``None``. A well-formed file returns
a typed ``HandoffData`` object with the expected field values.
"""
from __future__ import annotations

import json
from pathlib import Path

from drain_cycle.handoff import HANDOFF_FILE, HandoffData, read, write


def _valid_data() -> HandoffData:
    return HandoffData(
        pr_title="feat: add stack-ready worktrees",
        pr_body="## What\nAdds stacking.\n\n## Why\nNeeded for Graphite.\n\n## What to review\nworktree.py",
        findings={"critical": 0, "required": 1},
    )


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    data = _valid_data()
    write(tmp_path, data)
    result = read(tmp_path)

    assert result is not None
    assert result.pr_title == data.pr_title
    assert result.pr_body == data.pr_body
    assert result.findings == data.findings


def test_read_missing_file_returns_none(tmp_path: Path) -> None:
    assert read(tmp_path) is None


def test_read_malformed_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text("not valid json{{{")
    assert read(tmp_path) is None


def test_read_wrong_top_level_type_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(json.dumps([1, 2, 3]))
    assert read(tmp_path) is None


def test_read_missing_pr_title_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_body": "body", "findings": {"critical": 0, "required": 0}})
    )
    assert read(tmp_path) is None


def test_read_missing_pr_body_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_title": "title", "findings": {"critical": 0, "required": 0}})
    )
    assert read(tmp_path) is None


def test_read_missing_findings_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_title": "title", "pr_body": "body"})
    )
    assert read(tmp_path) is None


def test_read_findings_not_dict_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_title": "title", "pr_body": "body", "findings": [1, 2]})
    )
    assert read(tmp_path) is None


def test_read_pr_title_not_string_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_title": 42, "pr_body": "body", "findings": {}})
    )
    assert read(tmp_path) is None


def test_write_creates_file_at_expected_path(tmp_path: Path) -> None:
    write(tmp_path, _valid_data())
    assert (tmp_path / HANDOFF_FILE).exists()


def test_read_never_raises_on_empty_file(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text("")
    assert read(tmp_path) is None
