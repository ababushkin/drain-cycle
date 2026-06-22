"""NFR-6 marker contract — persona depth on every worker.

Feeds a Claude-Code review-stream fixture and a codex review-stream fixture and
proves both yield the correct active review persona through the ``_active``
marker path. The two workers differ in what their *stream* exposes; the marker
erases that difference, which is the whole point of putting persona identity on
a pack-written marker instead of parsing it out of the stream (design-doc
NFR-6).

It also pins the residual OQ-1 evidence captured in
``tests/fixtures/swimlanes/``: a real ``exec:review`` persona dispatch carries
no machine-readable persona in the stream — on Claude the ``Agent`` fan-out
input is ``{description, prompt}`` with no persona field, and on codex the
inline-sequential personas emit no tool boundary at all. The marker is therefore
the only worker-agnostic source of persona depth.
"""
from __future__ import annotations

import io
import json
import pathlib

from drain_cycle import swimlanes

_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "swimlanes"


def _load_stream(name: str) -> list[dict]:
    text = (_FIXTURES / name).read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _stream_active_step(events: list[dict]) -> str | None:
    tracker = swimlanes.StepTracker()
    for event in events:
        tracker.feed(event)
    return tracker.active


def _agent_inputs(events: list[dict]) -> list[dict]:
    inputs = []
    for event in events:
        for block in event["message"]["content"]:
            parsed = swimlanes.parse_tool_use(block)
            if parsed is not None and parsed[0] == "Agent":
                inputs.append(parsed[1])
    return inputs


def _write_marker(worktree: pathlib.Path, step: str, persona: str) -> None:
    worktree.joinpath("exec-state.json").write_text(
        json.dumps({"_active": {"step": step, "persona": persona}})
    )


def _render_with_marker(worktree: pathlib.Path) -> str:
    err = io.StringIO()
    renderer = swimlanes.build_renderer(err, worktree_path=worktree, tty=True)
    renderer.on_progress(1, 100, 1.0)
    return err.getvalue()


# --- Residual OQ-1 evidence: the stream cannot supply the persona ----------


def test_claude_agent_dispatch_carries_no_machine_readable_persona():
    events = _load_stream("claude-review-stream.jsonl")
    inputs = _agent_inputs(events)
    assert inputs, "the Claude fixture must contain the Agent persona fan-out"
    for inp in inputs:
        assert "persona" not in inp
        assert set(inp) <= {"description", "prompt"}


def test_codex_review_stream_exposes_no_step_or_persona_boundary():
    events = _load_stream("codex-review-stream.jsonl")
    # Inline-sequential personas emit no Skill/Agent tool boundary, so the
    # stream step path derives nothing — neither step nor persona.
    assert _stream_active_step(events) is None
    assert _agent_inputs(events) == []


# --- NFR-6: persona via the marker on both workers -------------------------


def test_claude_worker_yields_persona_via_marker(tmp_path):
    events = _load_stream("claude-review-stream.jsonl")
    # On Claude the stream gives the step but not the persona.
    assert _stream_active_step(events) == "exec:review"
    _write_marker(tmp_path, "review", "security-auditor")
    out = _render_with_marker(tmp_path)
    assert "review" in out
    assert "security-auditor" in out


def test_codex_worker_yields_persona_via_marker(tmp_path):
    # On codex the stream gives nothing; the marker is the only source of both
    # the step and the persona.
    _write_marker(tmp_path, "review", "security-auditor")
    out = _render_with_marker(tmp_path)
    assert "review" in out
    assert "security-auditor" in out


def test_codex_worker_without_marker_shows_no_persona(tmp_path):
    # NFR-6 contrast: with no marker, the codex worker has no persona depth at
    # all — proving the marker, not the stream, is what delivers it.
    err = io.StringIO()
    renderer = swimlanes.build_renderer(err, worktree_path=tmp_path, tty=True)
    for event in _load_stream("codex-review-stream.jsonl"):
        renderer.feed(event)
    renderer.on_progress(1, 100, 1.0)
    out = err.getvalue()
    assert "security-auditor" not in out
