"""Stream-derived swimlanes view: parser and renderer tests.

These tests pin the contract the live drain output relies on — the parser
turns a content block into the active `exec:*` step (or returns None when
there isn't one) and never raises on malformed input.
"""
from __future__ import annotations

from drain_cycle import swimlanes


def test_parse_skill_step_returns_skill_name_for_skill_tool_use():
    block = {
        "type": "tool_use",
        "name": "Skill",
        "input": {"skill": "exec:pickup"},
    }
    assert swimlanes.parse_skill_step(block) == "exec:pickup"


def test_parse_skill_step_returns_none_for_non_skill_tool_use():
    block = {
        "type": "tool_use",
        "name": "Bash",
        "input": {"command": "ls"},
    }
    assert swimlanes.parse_skill_step(block) is None


def test_parse_skill_step_returns_none_for_text_block():
    assert swimlanes.parse_skill_step({"type": "text", "text": "hi"}) is None


def test_parse_skill_step_returns_none_for_skill_missing_input():
    assert (
        swimlanes.parse_skill_step(
            {"type": "tool_use", "name": "Skill", "input": {}}
        )
        is None
    )


def test_parse_skill_step_handles_malformed_blocks():
    assert swimlanes.parse_skill_step({}) is None
    assert swimlanes.parse_skill_step({"type": "tool_use"}) is None
    assert swimlanes.parse_skill_step({"type": "tool_use", "name": "Skill"}) is None
    assert (
        swimlanes.parse_skill_step(
            {"type": "tool_use", "name": "Skill", "input": "oops"}
        )
        is None
    )


def test_parse_tool_use_returns_name_and_input_for_tool_use():
    block = {
        "type": "tool_use",
        "name": "Bash",
        "input": {"command": "ls", "description": "list"},
    }
    name, inp = swimlanes.parse_tool_use(block)
    assert name == "Bash"
    assert inp == {"command": "ls", "description": "list"}


def test_parse_tool_use_defaults_missing_name_to_question_mark():
    name, inp = swimlanes.parse_tool_use({"type": "tool_use"})
    assert name == "?"
    assert inp == {}


def test_parse_tool_use_defaults_non_dict_input_to_empty_dict():
    name, inp = swimlanes.parse_tool_use(
        {"type": "tool_use", "name": "X", "input": None}
    )
    assert name == "X"
    assert inp == {}


def test_parse_tool_use_returns_none_for_non_tool_use():
    assert swimlanes.parse_tool_use({"type": "text", "text": "hi"}) is None
    assert swimlanes.parse_tool_use({}) is None
    assert swimlanes.parse_tool_use(None) is None


def test_parse_skill_step_accepts_namespaced_plugin_skill():
    block = {
        "type": "tool_use",
        "name": "Skill",
        "input": {"skill": "shape-exec-build"},
    }
    assert swimlanes.parse_skill_step(block) == "shape-exec-build"
