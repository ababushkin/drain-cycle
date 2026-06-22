"""Marker degradation contracts (T3).

Two clean-degradation paths the swimlanes view must hold, both display-only and
non-gating (design-doc NFR-3):

* **Marker-miss (old pack)** — an `exec-state.json` with phase sections but no
  `_active` key falls back to the stream step path and never ages into a stale
  warning.
* **Stale marker (skill forgot to clear)** — a marker that stops updating while
  the run is demonstrably live (``on_progress`` keeps firing) past the staleness
  threshold dims the last-known active node and logs a single warning, never
  blocking the run.
"""
from __future__ import annotations

import io
import json
import logging

from drain_cycle import swimlanes


def _assistant_event(message_id: str, *blocks: dict) -> dict:
    return {"type": "assistant", "message": {"id": message_id, "content": list(blocks)}}


def _skill(skill_name: str) -> dict:
    return {"type": "tool_use", "name": "Skill", "input": {"skill": skill_name}}


def _write_marker(worktree, step: str, persona: str | None = None) -> None:
    payload: dict = {"_active": {"step": step}}
    if persona is not None:
        payload["_active"]["persona"] = persona
    (worktree / "exec-state.json").write_text(json.dumps(payload))


def test_old_pack_without_marker_degrades_to_stream_and_never_stales(tmp_path):
    # Old pack: a real exec-state.json carrying phase sections but no _active.
    (tmp_path / "exec-state.json").write_text(
        json.dumps({"pickup": {"issue_id": "ABA-1"}, "build": {"slices": []}})
    )
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(err, tty=True, worktree_path=tmp_path)
    renderer.feed(_assistant_event("m1", _skill("exec:build")))
    out = err.getvalue()
    assert "exec:build" in out  # stream step depth survives
    assert " / " not in out  # no persona on the old pack
    # Even far past the staleness threshold, a marker-miss never ages: there is
    # no marker to be stale, so no warning and no dimmed node.
    renderer.on_progress(9, 900, 9000.0)
    assert swimlanes.StepRenderer._STALE_GLYPH not in err.getvalue()


def test_stale_marker_dims_last_known_node_and_warns_once(tmp_path, caplog):
    _write_marker(tmp_path, "review", "code-quality")
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(
        err, tty=True, worktree_path=tmp_path, stale_threshold_s=120.0
    )
    # Fresh marker, run at 1 s — the node is bright/active.
    renderer.on_progress(1, 100, 1.0)
    assert swimlanes.StepRenderer._ACTIVE_GLYPH in err.getvalue()

    # The run advances well past the threshold with no marker rewrite → stale.
    with caplog.at_level(logging.WARNING, logger="drain_cycle.swimlanes"):
        renderer.on_progress(5, 500, 201.0)
        renderer.on_progress(6, 600, 202.0)
    out = err.getvalue()
    assert swimlanes.StepRenderer._STALE_GLYPH in out  # dimmed/aged active node
    assert "code-quality" in out  # last-known persona still shown
    warnings = [r for r in caplog.records if "stale" in r.getMessage().lower()]
    assert len(warnings) == 1  # log-only, exactly once


def test_stale_marker_clears_when_the_pack_writes_again(tmp_path):
    _write_marker(tmp_path, "review", "code-quality")
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(
        err, tty=True, worktree_path=tmp_path, stale_threshold_s=120.0
    )
    renderer.on_progress(1, 100, 1.0)
    renderer.on_progress(2, 200, 201.0)  # stale now
    assert renderer._marker_stale is True
    # The pack writes the next transition → fresh again.
    _write_marker(tmp_path, "verify", None)
    renderer.on_progress(3, 300, 202.0)
    assert renderer._marker_stale is False
