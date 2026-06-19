"""Stream-derived swimlanes view.

Reads the same stream-json events drain-cycle already drains and surfaces the
active `exec:*` step on stderr. The parser is the single source of truth for
"is this content block a Skill delegation, and which skill" — extracted from
``watch_format`` so the pane filter and the live-output renderer agree by
construction rather than by parallel implementations drifting apart.
"""
from __future__ import annotations

from typing import Any


def parse_tool_use(block: Any) -> tuple[str, dict[str, Any]] | None:
    """Return ``(name, input)`` for a ``tool_use`` content block, else ``None``.

    Centralises the recognition logic the pane filter and the swimlanes
    renderer both depend on. ``name`` defaults to ``"?"`` when the block
    carries no string ``name`` field — matching today's
    ``watch_format`` rendering of malformed tool_use events — and ``input``
    defaults to ``{}`` so callers can index it without a type guard.
    """
    if not isinstance(block, dict):
        return None
    if block.get("type") != "tool_use":
        return None
    name = block.get("name")
    if not isinstance(name, str):
        name = "?"
    raw = block.get("input")
    inp = raw if isinstance(raw, dict) else {}
    return name, inp


def parse_skill_step(block: Any) -> str | None:
    """Return the skill name when ``block`` is a Skill tool_use, else ``None``.

    Pre-build gate OQ-1 fixes the shape: a step delegation in the Claude Code
    stream is a content block of the form ::

        {"type": "tool_use", "name": "Skill", "input": {"skill": "<name>"}}

    Any other block — text, tool_result, a Bash tool_use, a Skill block with
    missing or non-string ``input.skill`` — is not a step transition and the
    caller's active step must stay where it is. Malformed input (non-dict
    block, ``input`` not a dict, missing keys) is treated as "not a step"
    rather than raising, because the renderer must never bring down the
    worker drain it feeds off.
    """
    parsed = parse_tool_use(block)
    if parsed is None:
        return None
    name, inp = parsed
    if name != "Skill":
        return None
    skill = inp.get("skill")
    if not isinstance(skill, str) or not skill:
        return None
    return skill


class StepTracker:
    """Track the currently-active ``exec:*`` step across stream events.

    State machine: feed assistant events, get back the new step name on a
    transition (or ``None`` when the active step is unchanged or the event
    carries no Skill delegation). The tracker keeps the first-seen order of
    every skill it has observed so the renderer can draw the full stepper
    row, and re-records a re-entered step at the tail so the operator sees
    they came back through it.

    Malformed events (non-dict, missing ``message``, ``content`` not a list)
    are silently ignored — the live renderer must never fault the worker
    drain it feeds off.
    """

    def __init__(self) -> None:
        self.history: list[str] = []
        self.active: str | None = None

    def feed(self, event: Any) -> str | None:
        if not isinstance(event, dict):
            return None
        if event.get("type") != "assistant":
            return None
        message = event.get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if not isinstance(content, list):
            return None
        new_active: str | None = None
        for block in content:
            step = parse_skill_step(block)
            if step is None:
                continue
            new_active = step
        if new_active is None or new_active == self.active:
            return None
        self.active = new_active
        self.history.append(new_active)
        return new_active
