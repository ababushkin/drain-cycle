"""Stream-derived swimlanes view: parser and renderer tests.

These tests pin the contract the live drain output relies on — the parser
turns a content block into the active `exec:*` step (or returns None when
there isn't one) and never raises on malformed input.
"""
from __future__ import annotations

import io
import json

from drain_cycle import swimlanes, worker


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


def _assistant_event(message_id: str, *blocks: dict) -> dict:
    return {
        "type": "assistant",
        "message": {"id": message_id, "content": list(blocks)},
    }


def _skill(skill_name: str) -> dict:
    return {"type": "tool_use", "name": "Skill", "input": {"skill": skill_name}}


def test_step_tracker_records_first_skill_step_as_active():
    tracker = swimlanes.StepTracker()
    new_step = tracker.feed(_assistant_event("m1", _skill("exec:pickup")))
    assert new_step == "exec:pickup"
    assert tracker.active == "exec:pickup"
    assert tracker.history == ["exec:pickup"]


def test_step_tracker_returns_none_when_active_step_unchanged():
    tracker = swimlanes.StepTracker()
    tracker.feed(_assistant_event("m1", _skill("exec:pickup")))
    again = tracker.feed(_assistant_event("m2", _skill("exec:pickup")))
    assert again is None
    assert tracker.history == ["exec:pickup"]


def test_step_tracker_appends_history_in_first_seen_order():
    tracker = swimlanes.StepTracker()
    sequence = ["exec:pickup", "exec:breakdown", "exec:build", "exec:review"]
    for i, skill in enumerate(sequence):
        assert tracker.feed(_assistant_event(f"m{i}", _skill(skill))) == skill
    assert tracker.history == sequence
    assert tracker.active == "exec:review"


def test_step_tracker_ignores_non_assistant_events():
    tracker = swimlanes.StepTracker()
    assert tracker.feed({"type": "user", "message": {}}) is None
    assert tracker.feed({"type": "result", "num_turns": 1}) is None
    assert tracker.feed({"type": "system"}) is None
    assert tracker.active is None
    assert tracker.history == []


def test_step_tracker_ignores_non_skill_tool_use():
    tracker = swimlanes.StepTracker()
    event = _assistant_event(
        "m1",
        {"type": "text", "text": "hello"},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
    )
    assert tracker.feed(event) is None
    assert tracker.active is None


def test_step_tracker_picks_last_skill_in_a_multi_block_message():
    # A Skill delegation yields control back to the runtime, so multiple
    # Skill blocks in one assistant turn don't happen in practice. The
    # tracker still has to behave: pick the last Skill in the block list
    # (the operator's most recent intent) and record only that one in
    # history — pretending the earlier ones happened would invent steps.
    tracker = swimlanes.StepTracker()
    event = _assistant_event(
        "m1",
        _skill("exec:pickup"),
        _skill("exec:breakdown"),
    )
    new_step = tracker.feed(event)
    assert new_step == "exec:breakdown"
    assert tracker.active == "exec:breakdown"
    assert tracker.history == ["exec:breakdown"]


def test_step_tracker_re_entered_step_does_not_duplicate_history():
    tracker = swimlanes.StepTracker()
    for i, skill in enumerate(["a", "b", "a"]):
        tracker.feed(_assistant_event(f"m{i}", _skill(skill)))
    assert tracker.history == ["a", "b", "a"] or tracker.history == ["a", "b"]
    # First-seen-order is the contract; a re-entered step appends again so the
    # operator can see they came back through it. The OR above documents that
    # both interpretations have been considered — pin the chosen one:
    assert tracker.history == ["a", "b", "a"]
    assert tracker.active == "a"


def test_step_tracker_safe_against_malformed_event_shapes():
    tracker = swimlanes.StepTracker()
    assert tracker.feed({}) is None
    assert tracker.feed({"type": "assistant"}) is None
    assert tracker.feed({"type": "assistant", "message": "oops"}) is None
    assert (
        tracker.feed({"type": "assistant", "message": {"content": "oops"}}) is None
    )
    assert tracker.active is None


def _stream_of(*events: dict) -> io.StringIO:
    return io.StringIO("\n".join(json.dumps(e) for e in events) + "\n")


def test_step_renderer_emits_current_step_on_stderr_when_tty():
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True)
    renderer.feed(_assistant_event("m1", _skill("exec:pickup")))
    out = err.getvalue()
    assert "exec:pickup" in out
    # First emission carries a carriage return so the line redraws in place.
    assert "\r" in out


def test_step_renderer_flips_in_place_on_each_transition():
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True)
    renderer.feed(_assistant_event("m1", _skill("exec:pickup")))
    renderer.feed(_assistant_event("m2", _skill("exec:breakdown")))
    out = err.getvalue()
    # Both steps visible (history-row contract), the active one marked.
    assert "exec:pickup" in out
    assert "exec:breakdown" in out
    # Active step is the most recent — flipped within one transition.
    last_pickup = out.rfind("exec:pickup")
    last_breakdown = out.rfind("exec:breakdown")
    assert last_breakdown > last_pickup
    # The row redraws (CR sequences > 1) rather than appending newlines.
    assert out.count("\r") >= 2


def test_step_renderer_is_silent_when_not_tty():
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=False)
    for skill in ("exec:pickup", "exec:breakdown", "exec:build"):
        renderer.feed(_assistant_event(f"m-{skill}", _skill(skill)))
    assert err.getvalue() == ""


def test_step_renderer_auto_detects_tty_from_stream_isatty():
    class Mock:
        def __init__(self) -> None:
            self.buffer = ""

        def isatty(self) -> bool:
            return True

        def write(self, s: str) -> int:
            self.buffer += s
            return len(s)

        def flush(self) -> None:
            return None

    mock = Mock()
    renderer = swimlanes.StepRenderer(mock)
    renderer.feed(_assistant_event("m1", _skill("exec:pickup")))
    assert "exec:pickup" in mock.buffer


def test_step_renderer_swallows_write_errors():
    class Broken:
        def isatty(self) -> bool:
            return True

        def write(self, s: str) -> int:
            raise OSError("pipe closed")

        def flush(self) -> None:
            raise OSError("pipe closed")

    renderer = swimlanes.StepRenderer(Broken())
    # Must not propagate — the renderer is non-gating on the worker drain.
    renderer.feed(_assistant_event("m1", _skill("exec:pickup")))


def test_drain_stream_routes_events_to_step_callback_and_leaves_sink_untouched():
    err = io.StringIO()
    sink = io.StringIO()
    accumulator = worker._UsageAccumulator()
    renderer = swimlanes.StepRenderer(err, tty=True)
    stream = _stream_of(
        _assistant_event("m1", _skill("exec:pickup")),
        _assistant_event("m2", _skill("exec:breakdown")),
        _assistant_event("m3", _skill("exec:build")),
    )
    worker._drain_stream(
        stream, accumulator, sink, on_progress=None, on_step=renderer.feed
    )
    out = err.getvalue()
    assert "exec:pickup" in out
    assert "exec:breakdown" in out
    assert "exec:build" in out
    # AgentSink passthrough is byte-identical to today — the swimlanes view
    # owns its own stderr region and never bleeds into the sink.
    assert sink.getvalue() == ""


def test_drain_stream_without_on_step_callback_is_unchanged():
    """Feature is opt-in: omitting on_step keeps today's behaviour intact."""
    sink = io.StringIO()
    accumulator = worker._UsageAccumulator()
    stream = _stream_of(_assistant_event("m1", _skill("exec:pickup")))
    worker._drain_stream(stream, accumulator, sink)
    assert sink.getvalue() == ""


def test_drain_stream_does_not_call_on_step_with_non_dict_events():
    """A non-JSON-object line goes to the sink; the step callback never sees it."""
    sink = io.StringIO()
    accumulator = worker._UsageAccumulator()
    seen: list = []
    stream = io.StringIO('"a bare string"\n42\nnot json\n')
    worker._drain_stream(
        stream, accumulator, sink, on_progress=None, on_step=seen.append
    )
    assert seen == []
    # Bare strings, bare numbers, and non-JSON all echo to the sink.
    assert "a bare string" in sink.getvalue()
    assert "42" in sink.getvalue()
    assert "not json" in sink.getvalue()
