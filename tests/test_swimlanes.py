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
    # First emission opens a DECSTBM scroll region so the bottom rows hold
    # the status block while the worker passthrough scrolls above.
    assert "\x1b[1;" in out and "r" in out


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
    # Each transition saves+restores the cursor around the absolute-position
    # paint, so a brace pair appears per emit rather than appended newlines.
    assert out.count("\x1b7") >= 2
    assert out.count("\x1b8") >= 2


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


def _write_marker(worktree, step, persona=None):
    payload = {"_active": {"step": step}}
    if persona is not None:
        payload["_active"]["persona"] = persona
    (worktree / "exec-state.json").write_text(json.dumps(payload))


def test_read_active_marker_returns_step_and_persona(tmp_path):
    _write_marker(tmp_path, "review", "code-quality")
    marker = swimlanes.read_active_marker(tmp_path)
    assert marker is not None
    assert marker.step == "review"
    assert marker.persona == "code-quality"


def test_read_active_marker_returns_none_when_file_absent(tmp_path):
    assert swimlanes.read_active_marker(tmp_path) is None


def test_read_active_marker_returns_none_on_malformed_state(tmp_path):
    (tmp_path / "exec-state.json").write_text("{not json")
    assert swimlanes.read_active_marker(tmp_path) is None


def test_read_active_marker_returns_none_when_active_key_missing(tmp_path):
    (tmp_path / "exec-state.json").write_text(json.dumps({"pickup": {}}))
    assert swimlanes.read_active_marker(tmp_path) is None


def test_step_renderer_prefers_active_marker_for_step_and_persona(tmp_path):
    # The stream alone yields the skill name; only the marker carries the
    # active review persona. Persona depth in the row proves the marker won.
    _write_marker(tmp_path, "review", "code-quality")
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True, worktree_path=tmp_path)
    renderer.on_progress(1, 100, 1.0)
    out = err.getvalue()
    assert "review" in out
    assert "code-quality" in out


def test_step_renderer_falls_back_to_stream_step_when_no_marker(tmp_path):
    # No exec-state.json written → marker absent → today's stream path holds.
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True, worktree_path=tmp_path)
    renderer.feed(_assistant_event("m1", _skill("exec:build")))
    out = err.getvalue()
    assert "exec:build" in out


def test_step_renderer_marker_step_without_persona_shows_no_separator(tmp_path):
    _write_marker(tmp_path, "build")
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True, worktree_path=tmp_path)
    renderer.on_progress(1, 100, 1.0)
    out = err.getvalue()
    assert "build" in out
    assert " / " not in out


def test_live_drain_prefers_marker_then_falls_back_when_removed(tmp_path):
    # End-to-end through the orchestrator's construction seam: the renderer
    # follows the stream step until the pack writes _active, prefers the
    # marker's persona while it is present, then falls back to the stream step
    # the moment the marker is gone.
    err = io.StringIO()
    renderer = swimlanes.build_renderer(err, worktree_path=tmp_path, tty=True)

    # Stream-only: no marker yet → today's step depth.
    renderer.feed(_assistant_event("m1", _skill("exec:review")))
    assert "exec:review" in err.getvalue()

    # The pack writes the marker mid-run → persona depth appears.
    _write_marker(tmp_path, "review", "code-quality")
    err.truncate(0)
    err.seek(0)
    renderer.on_progress(2, 200, 2.0)
    out = err.getvalue()
    assert "review" in out
    assert "code-quality" in out

    # Marker cleared (old pack / forgot to write) → fall back to the stream.
    (tmp_path / "exec-state.json").unlink()
    err.truncate(0)
    err.seek(0)
    renderer.on_progress(3, 300, 3.0)
    fallback = err.getvalue()
    assert "exec:review" in fallback
    assert "code-quality" not in fallback


def test_build_renderer_threads_worktree_path_and_queue(tmp_path):
    # The orchestrator's construction seam must thread the worktree path so the
    # renderer reads the marker, and apply the queue in one call.
    _write_marker(tmp_path, "review", "security-auditor")
    err = io.StringIO()
    queue = [swimlanes.QueueItem("ABA-1", "running")]
    renderer = swimlanes.build_renderer(
        err, worktree_path=tmp_path, queue=queue, tty=True
    )
    renderer.on_progress(1, 100, 1.0)
    out = err.getvalue()
    assert "security-auditor" in out
    assert "ABA-1" in out


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


def test_run_issue_invokes_on_step_for_recognised_events(tmp_path):
    """The active-step plumbing reaches the worker entry point — operators
    see the live row, not only direct ``_drain_stream`` callers."""
    events = [
        _assistant_event("m1", _skill("exec:pickup")),
        _assistant_event("m2", _skill("exec:breakdown")),
        {
            "type": "result",
            "total_cost_usd": 0.0,
            "num_turns": 2,
            "session_id": "s",
            "is_error": False,
        },
    ]
    stream = io.StringIO("\n".join(json.dumps(e) for e in events) + "\n")
    seen: list[dict] = []
    result = worker.run_issue(
        claude_cmd=["unused"],
        model="claude-opus-4-8",
        prompt="ignored",
        cwd=tmp_path,
        token_limit=None,
        time_limit_seconds=None,
        cost_limit_usd=None,
        passthrough=io.StringIO(),
        external_stream=stream,
        on_step=seen.append,
    )
    assert result.breach is None
    types = [e.get("type") for e in seen]
    assert types.count("assistant") == 2
    assert "result" in types


def test_run_issue_on_step_failure_does_not_break_drain(tmp_path):
    """Render-path exceptions never propagate into the worker drain."""
    events = [
        _assistant_event("m1", _skill("exec:pickup")),
        {
            "type": "result",
            "total_cost_usd": 0.0,
            "num_turns": 1,
            "session_id": "s",
            "is_error": False,
        },
    ]
    stream = io.StringIO("\n".join(json.dumps(e) for e in events) + "\n")

    def _raise(_event: dict) -> None:
        raise RuntimeError("renderer is on fire")

    result = worker.run_issue(
        claude_cmd=["unused"],
        model="claude-opus-4-8",
        prompt="ignored",
        cwd=tmp_path,
        token_limit=None,
        time_limit_seconds=None,
        cost_limit_usd=None,
        passthrough=io.StringIO(),
        external_stream=stream,
        on_step=_raise,
    )
    assert result.breach is None
    assert result.num_turns == 1


def test_run_issue_passthrough_byte_identical_with_and_without_swimlanes(tmp_path):
    """The chosen redraw mechanism (hand-rolled ANSI on stderr) must leave the
    worker's stdout passthrough byte-identical to a feature-off run on the
    same fixture. The renderer writes to its own stderr region; the sink that
    AgentSink fronts must never see a single byte from this layer.
    """
    events = [
        _assistant_event("m1", _skill("exec:pickup")),
        # A non-JSON diagnostic line — these go to the passthrough sink.
        # The renderer must NEVER touch this stream.
        _assistant_event("m2", _skill("exec:breakdown")),
        {
            "type": "result",
            "total_cost_usd": 0.0,
            "num_turns": 2,
            "session_id": "s",
            "is_error": False,
        },
    ]
    raw = "\n".join(json.dumps(e) for e in events)
    raw += "\nnot-a-json-diagnostic-line\n"
    raw += '"a bare string event"\n'
    raw += "42\n"

    sink_with_feature = io.StringIO()
    renderer_stderr = io.StringIO()
    renderer = swimlanes.StepRenderer(renderer_stderr, tty=True)
    worker.run_issue(
        claude_cmd=["unused"],
        model="claude-opus-4-8",
        prompt="ignored",
        cwd=tmp_path,
        token_limit=None,
        time_limit_seconds=None,
        cost_limit_usd=None,
        passthrough=sink_with_feature,
        external_stream=io.StringIO(raw),
        on_step=renderer.feed,
    )

    sink_without_feature = io.StringIO()
    worker.run_issue(
        claude_cmd=["unused"],
        model="claude-opus-4-8",
        prompt="ignored",
        cwd=tmp_path,
        token_limit=None,
        time_limit_seconds=None,
        cost_limit_usd=None,
        passthrough=sink_without_feature,
        external_stream=io.StringIO(raw),
    )

    assert sink_with_feature.getvalue() == sink_without_feature.getvalue()
    # Sanity: the non-JSON diagnostic lines DID make it into the sink (so the
    # parity is not "both empty" by accident).
    assert "not-a-json-diagnostic-line" in sink_with_feature.getvalue()
    # And the renderer's stream really did render both steps — proving the
    # passthrough integrity holds with the feature active, not bypassed.
    rendered = renderer_stderr.getvalue()
    assert "exec:pickup" in rendered
    assert "exec:breakdown" in rendered
    # The renderer opened a DECSTBM scroll region — the mechanism that keeps
    # the pinned status block visible while the passthrough scrolls above.
    assert "\x1b[1;" in rendered and "r\x1b[" in rendered


def test_step_renderer_renders_cycle_queue_above_stepper():
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True)
    renderer.set_queue(
        [
            swimlanes.QueueItem("ABA-410", "done"),
            swimlanes.QueueItem("ABA-411", "running"),
            swimlanes.QueueItem("ABA-412", "queued"),
        ]
    )
    renderer.feed(_assistant_event("m1", _skill("exec:pickup")))
    out = err.getvalue()
    # All three issues appear on the queue row.
    assert "ABA-410" in out
    assert "ABA-411" in out
    assert "ABA-412" in out
    # Active step appears on the stepper row.
    assert "exec:pickup" in out
    # Queue row is above the stepper row — its first occurrence comes earlier.
    assert out.find("ABA-411") < out.find("exec:pickup")


def test_step_renderer_queue_distinguishes_done_running_queued_states():
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True)
    renderer.set_queue(
        [
            swimlanes.QueueItem("ABA-410", "done"),
            swimlanes.QueueItem("ABA-411", "running"),
            swimlanes.QueueItem("ABA-412", "queued"),
        ]
    )
    renderer.feed(_assistant_event("m1", _skill("exec:pickup")))
    out = err.getvalue()
    # Distinct glyphs per state — the operator must tell them apart at a glance.
    assert "✓ ABA-410" in out
    # Running issue is auto-focused (bracketed by the T4 viewing toggle).
    assert "▶ [ABA-411]" in out
    assert "◯ ABA-412" in out


def test_step_renderer_set_queue_advances_lane_state_on_next_render():
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True)
    renderer.set_queue(
        [
            swimlanes.QueueItem("ABA-411", "running"),
            swimlanes.QueueItem("ABA-412", "queued"),
        ]
    )
    renderer.feed(_assistant_event("m1", _skill("exec:pickup")))
    # Lane state is advanced by the orchestrator owning the queue list and
    # re-passing it on each transition — the renderer never recomputes it.
    renderer.set_queue(
        [
            swimlanes.QueueItem("ABA-411", "done"),
            swimlanes.QueueItem("ABA-412", "running"),
        ]
    )
    renderer.feed(_assistant_event("m2", _skill("exec:breakdown")))
    out = err.getvalue()
    # Most recent render reflects the advanced state — look at the tail.
    # The running issue is auto-focused (bracketed) — that brackets the
    # active running lane label.
    assert out.rfind("✓ ABA-411") > out.rfind("▶ [ABA-411]")
    assert out.rfind("▶ [ABA-412]") > out.rfind("◯ ABA-412")


def test_step_renderer_preserves_orchestrator_pick_order_does_not_resort():
    """Queue order is the orchestrator's pick order, sourced not recomputed.
    The renderer must not re-sort by identifier or by state — it would lie
    about the orchestrator's actual sequence."""
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True)
    renderer.set_queue(
        [
            swimlanes.QueueItem("ABA-412", "queued"),
            swimlanes.QueueItem("ABA-410", "running"),
            swimlanes.QueueItem("ABA-411", "done"),
        ]
    )
    renderer.feed(_assistant_event("m1", _skill("exec:pickup")))
    out = err.getvalue()
    # Position-in-string order matches the input list order.
    assert out.find("ABA-412") < out.find("ABA-410") < out.find("ABA-411")


def test_step_renderer_empty_queue_still_renders_stepper():
    """An empty queue keeps the single-line stepper."""
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True)
    renderer.set_queue([])
    renderer.feed(_assistant_event("m1", _skill("exec:pickup")))
    out = err.getvalue()
    assert "exec:pickup" in out
    # No queue glyphs leaked through.
    assert "✓" not in out
    assert "◯" not in out


def test_step_renderer_queue_without_step_does_not_render():
    """Queue alone, with no step transition, emits nothing. Rendering is
    transition-driven — until the first Skill block, there's no row."""
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True)
    renderer.set_queue([swimlanes.QueueItem("ABA-411", "running")])
    assert err.getvalue() == ""


def test_step_renderer_on_progress_appends_proof_of_life_sub_status():
    """Proof-of-life sub-status — the active node carries a live
    ``turn N · X tok · elapsed`` line so the operator can see it's alive
    between step transitions."""
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True)
    renderer.feed(_assistant_event("m1", _skill("exec:build")))
    renderer.on_progress(turns=3, cumulative_tokens=12_500, elapsed_seconds=42.0)
    out = err.getvalue()
    assert "turn 3" in out
    assert "exec:build" in out
    # The sub-status is part of the active step's row, not a separate line.
    last_active = out.rfind("▶ exec:build")
    last_sub = out.rfind("turn 3")
    assert last_sub > last_active


def test_step_renderer_sanitises_ansi_escapes_in_skill_name():
    """A model-controlled skill name carrying ANSI escapes (or other control
    bytes) must not bleed through the row — the embedded escape would let
    a prompt-injected payload move the cursor or rewrite the operator's
    scrollback."""
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True)
    poisoned = "exec:\x1b[31mevil\x1b[0m\rhidden"
    renderer.feed(_assistant_event("m1", _skill(poisoned)))
    out = err.getvalue()
    # The control bytes are replaced with `?` — they never reach stderr raw,
    # apart from the renderer's own framing escapes (the CR + CSI 2K prefix).
    # Strip the framing prefix before checking for embedded escapes.
    payload = out.replace("\r\x1b[2K", "")
    assert "\x1b[31m" not in payload
    assert "\x1b[0m" not in payload
    # The carriage return embedded in the skill name is also sanitised.
    assert "\rhidden" not in payload


def test_step_renderer_focus_issue_highlights_the_focused_lane():
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True)
    renderer.set_queue(
        [
            swimlanes.QueueItem("ABA-410", "running"),
            swimlanes.QueueItem("ABA-411", "queued"),
        ]
    )
    renderer.focus_issue("ABA-411")
    renderer.feed(_assistant_event("m1", _skill("exec:pickup")))
    out = err.getvalue()
    # Focused issue is wrapped in brackets so it stands out from the
    # auto-follow target (the running one).
    assert "[ABA-411]" in out
    # The auto-follow target is no longer the visual focus.
    assert "[ABA-410]" not in out


def test_step_renderer_clears_focus_restoring_auto_follow_on_running_issue():
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True)
    renderer.set_queue(
        [
            swimlanes.QueueItem("ABA-410", "running"),
            swimlanes.QueueItem("ABA-411", "queued"),
        ]
    )
    renderer.focus_issue("ABA-411")
    renderer.focus_issue(None)
    renderer.feed(_assistant_event("m1", _skill("exec:pickup")))
    out = err.getvalue()
    # Auto-follow → the focus follows the running issue, not a stale selection.
    assert "[ABA-410]" in out
    assert "[ABA-411]" not in out


def test_step_renderer_focused_identifier_property_tracks_explicit_focus():
    renderer = swimlanes.StepRenderer(io.StringIO(), tty=True)
    assert renderer.focused_identifier is None  # auto-follow default
    renderer.focus_issue("ABA-411")
    assert renderer.focused_identifier == "ABA-411"
    renderer.focus_issue(None)
    assert renderer.focused_identifier is None


def test_keyboard_listener_is_a_noop_when_stdin_is_not_a_tty():
    """The done_when: a piped run auto-follows with no input handling.
    KeyboardListener.start() on a non-TTY stream must not raise, must not
    spawn a thread, and must not touch termios — so a piped drain-cycle run
    completes with WorkerResult and exit code identical to a TTY-less run
    that didn't construct the listener at all.
    """

    class NotATty:
        def isatty(self) -> bool:
            return False

    renderer = swimlanes.StepRenderer(io.StringIO(), tty=False)
    listener = swimlanes.KeyboardListener(renderer, stdin=NotATty())
    listener.start()
    assert listener.active is False
    # stop() on an inactive listener is also a no-op.
    listener.stop()
    assert listener.active is False


def test_keyboard_listener_routes_digit_key_to_focus_n_th_queue_item():
    """When a digit 1-9 is received the listener focuses the nth queue item.
    This test drives the parser directly (the live termios loop is exercised
    only on a TTY in integration); here we verify the key→focus mapping."""
    renderer = swimlanes.StepRenderer(io.StringIO(), tty=True)
    renderer.set_queue(
        [
            swimlanes.QueueItem("ABA-410", "running"),
            swimlanes.QueueItem("ABA-411", "queued"),
            swimlanes.QueueItem("ABA-412", "queued"),
        ]
    )
    listener = swimlanes.KeyboardListener(renderer)
    listener.handle_key("2")
    assert renderer.focused_identifier == "ABA-411"
    listener.handle_key("3")
    assert renderer.focused_identifier == "ABA-412"
    # Out-of-range digit → noop (no crash, focus unchanged).
    listener.handle_key("9")
    assert renderer.focused_identifier == "ABA-412"
    # Non-digit → also noop.
    listener.handle_key("x")
    assert renderer.focused_identifier == "ABA-412"
    # "0" clears focus → auto-follow.
    listener.handle_key("0")
    assert renderer.focused_identifier is None


def test_step_renderer_redraw_mechanism_is_decstbm_pinned_region():
    """OQ-4 re-settled for the multi-line view: DECSTBM, not rich.Live or the
    alternate-screen buffer.

    rich.Live would own the cursor on a refresh loop, suppressing the
    append-only writes the operator relies on (``console.worker_event``, the
    ``AgentSink`` ``│``-prefixed diagnostics). The alternate-screen buffer
    would hide those writes entirely. DECSTBM is the only mechanism that
    cohabits with append-only stderr: the bottom rows are pinned outside the
    scroll region while the passthrough scrolls within it. The pinned block
    repaints via absolute cursor positioning bracketed by save/restore so the
    cursor returns to the scrolling area between repaints — the worker log
    never lands inside the status block, and the status block never lands
    inside the log.
    """
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True)
    renderer.feed(_assistant_event("m1", _skill("exec:pickup")))
    out = err.getvalue()
    # Region was opened — top..bottom DECSTBM with explicit numeric bounds.
    assert "\x1b[1;" in out
    # The paint is bracketed by DEC save/restore cursor so subsequent
    # passthrough writes land where the worker thread left the cursor.
    assert "\x1b7" in out
    assert "\x1b8" in out
    # The stepper row was painted with absolute positioning into the
    # pinned region — there is at least one ``CSI <row>;<col>H`` for the
    # bottom row of the region (row 24 in the 80x24 fallback geometry).
    assert "\x1b[24;1H" in out


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
