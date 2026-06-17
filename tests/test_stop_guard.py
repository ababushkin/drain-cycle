"""Tests for ``drain_cycle.stop_guard``.

The guard must:

* No-op when no marker is present (interactive sessions are unaffected).
* Block a stop when the worktree is dirty or, in stack mode, lacks a
  valid handoff — re-injecting the completion sequence.
* Pass through when the completion artefacts are in place.
* After ``max_blocks`` re-injections, give up: write the tripped marker
  with a description and let the session exit cleanly.
"""
from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from drain_cycle import stop_guard
from drain_cycle.handoff import HandoffData, PullRequest, write as write_handoff


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def _commit(path: Path, name: str, contents: str = "x") -> None:
    (path / name).write_text(contents)
    subprocess.run(["git", "add", name], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=path, check=True)


def _valid_handoff() -> HandoffData:
    return HandoffData(
        pr_urls=(PullRequest(title="feat: x", url="https://github.com/o/r/pull/1"),)
    )


def test_no_marker_is_noop(tmp_path: Path) -> None:
    _git_init(tmp_path)
    decision = stop_guard.evaluate(tmp_path, stop_hook_active=False)
    assert decision.block_reason is None
    assert decision.tripped_reason is None
    assert not (tmp_path / stop_guard.TRIPPED_FILE).exists()


def test_stack_mode_clean_tree_with_handoff_passes_through(tmp_path: Path) -> None:
    _git_init(tmp_path)
    _commit(tmp_path, "a.txt")
    stop_guard.write_marker(tmp_path, mode="stack")
    write_handoff(tmp_path, _valid_handoff())
    # The marker and handoff are tracked-not, leave tree dirty by status —
    # in practice both are gitignored, so simulate by committing them away
    # or by adding to .gitignore. Add a .gitignore here.
    (tmp_path / ".gitignore").write_text(
        f"{stop_guard.MARKER_FILE}\n.drain-handoff.json\n.drain-guard-tripped\n"
    )
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "ignore"], cwd=tmp_path, check=True)

    decision = stop_guard.evaluate(tmp_path, stop_hook_active=False)
    assert decision.block_reason is None
    assert decision.tripped_reason is None


def test_stack_mode_dirty_tree_blocks(tmp_path: Path) -> None:
    _git_init(tmp_path)
    _commit(tmp_path, "a.txt")
    stop_guard.write_marker(tmp_path, mode="stack")
    (tmp_path / "dirty.py").write_text("print(1)")

    decision = stop_guard.evaluate(tmp_path, stop_hook_active=False)
    assert decision.block_reason is not None
    assert "commit" in decision.block_reason.lower()
    assert "exec-state.json" in decision.block_reason
    # count incremented
    cfg = stop_guard.read_marker(tmp_path)
    assert cfg is not None and cfg.count == 1


def test_stack_mode_missing_handoff_blocks(tmp_path: Path) -> None:
    _git_init(tmp_path)
    _commit(tmp_path, "a.txt")
    (tmp_path / ".gitignore").write_text(
        f"{stop_guard.MARKER_FILE}\n.drain-guard-tripped\n"
    )
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "ignore"], cwd=tmp_path, check=True)
    stop_guard.write_marker(tmp_path, mode="stack")

    decision = stop_guard.evaluate(tmp_path, stop_hook_active=False)
    # Tree is clean but handoff missing → block in stack mode.
    assert decision.block_reason is not None
    assert "exec-state.json" in decision.block_reason


def test_push_mode_clean_tree_passes_through(tmp_path: Path) -> None:
    _git_init(tmp_path)
    _commit(tmp_path, "a.txt")
    (tmp_path / ".gitignore").write_text(
        f"{stop_guard.MARKER_FILE}\n.drain-guard-tripped\n"
    )
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "ignore"], cwd=tmp_path, check=True)
    stop_guard.write_marker(tmp_path, mode="push")

    decision = stop_guard.evaluate(tmp_path, stop_hook_active=False)
    assert decision.block_reason is None
    assert decision.tripped_reason is None


def test_max_blocks_writes_tripped_marker(tmp_path: Path) -> None:
    _git_init(tmp_path)
    _commit(tmp_path, "a.txt")
    stop_guard.write_marker(tmp_path, mode="stack", max_blocks=2)
    (tmp_path / "dirty.py").write_text("x")

    # Two blocks consumed, third call gives up.
    d1 = stop_guard.evaluate(tmp_path, stop_hook_active=False)
    d2 = stop_guard.evaluate(tmp_path, stop_hook_active=False)
    d3 = stop_guard.evaluate(tmp_path, stop_hook_active=False)
    assert d1.block_reason is not None
    assert d2.block_reason is not None
    assert d3.block_reason is None
    assert d3.tripped_reason is not None
    assert (tmp_path / stop_guard.TRIPPED_FILE).read_text() == d3.tripped_reason


def test_stop_hook_active_after_first_block_trips(tmp_path: Path) -> None:
    """Claude's own loop-guard flag forces an exit even before max_blocks."""
    _git_init(tmp_path)
    _commit(tmp_path, "a.txt")
    stop_guard.write_marker(tmp_path, mode="stack", max_blocks=5)
    (tmp_path / "dirty.py").write_text("x")

    d1 = stop_guard.evaluate(tmp_path, stop_hook_active=False)
    assert d1.block_reason is not None
    # Now Claude reports stop_hook_active — guard should bail rather than block again.
    d2 = stop_guard.evaluate(tmp_path, stop_hook_active=True)
    assert d2.block_reason is None
    assert d2.tripped_reason is not None


def test_write_marker_clears_stale_tripped(tmp_path: Path) -> None:
    (tmp_path / stop_guard.TRIPPED_FILE).write_text("old")
    stop_guard.write_marker(tmp_path, mode="stack")
    assert not (tmp_path / stop_guard.TRIPPED_FILE).exists()


def test_read_marker_invalid_returns_none(tmp_path: Path) -> None:
    (tmp_path / stop_guard.MARKER_FILE).write_text("garbage")
    assert stop_guard.read_marker(tmp_path) is None


def test_run_emits_block_decision_to_stdout(tmp_path: Path) -> None:
    _git_init(tmp_path)
    _commit(tmp_path, "a.txt")
    stop_guard.write_marker(tmp_path, mode="stack")
    (tmp_path / "dirty.py").write_text("x")

    stdin = io.StringIO(json.dumps({"cwd": str(tmp_path), "stop_hook_active": False}))
    stdout = io.StringIO()
    rc = stop_guard.run(stdin, stdout, cwd=tmp_path)

    assert rc == 0
    payload = json.loads(stdout.getvalue())
    assert payload["decision"] == "block"
    assert "drain-cycle stop-guard" in payload["reason"]


def test_run_no_output_when_passing_through(tmp_path: Path) -> None:
    stdin = io.StringIO(json.dumps({"cwd": str(tmp_path)}))
    stdout = io.StringIO()
    rc = stop_guard.run(stdin, stdout, cwd=tmp_path)
    assert rc == 0
    assert stdout.getvalue() == ""


def test_run_handles_invalid_stdin_payload(tmp_path: Path) -> None:
    stdin = io.StringIO("not json")
    stdout = io.StringIO()
    rc = stop_guard.run(stdin, stdout, cwd=tmp_path)
    assert rc == 0
    assert stdout.getvalue() == ""


def test_read_tripped_returns_reason(tmp_path: Path) -> None:
    (tmp_path / stop_guard.TRIPPED_FILE).write_text("some reason\n")
    assert stop_guard.read_tripped(tmp_path) == "some reason"


def test_read_tripped_missing_returns_none(tmp_path: Path) -> None:
    assert stop_guard.read_tripped(tmp_path) is None


def test_own_artefacts_do_not_count_as_dirty(tmp_path: Path) -> None:
    """The marker file itself is untracked — it must not be seen as work."""
    _git_init(tmp_path)
    _commit(tmp_path, "a.txt")
    stop_guard.write_marker(tmp_path, mode="stack")
    write_handoff(tmp_path, _valid_handoff())
    # Marker and handoff are both untracked; nothing else is dirty.
    decision = stop_guard.evaluate(tmp_path, stop_hook_active=False)
    assert decision.block_reason is None
    assert decision.tripped_reason is None


def test_exec_state_file_does_not_count_as_dirty(tmp_path: Path) -> None:
    """exec-state.json is a drain-cycle artefact — untracked, must not trip guard."""
    import json as _json
    from drain_cycle.handoff import EXEC_STATE_FILE
    _git_init(tmp_path)
    _commit(tmp_path, "a.txt")
    stop_guard.write_marker(tmp_path, mode="stack")
    # Write exec-state.json as the handoff (the new primary file).
    (tmp_path / EXEC_STATE_FILE).write_text(
        _json.dumps({"pr_urls": [{"title": "feat", "url": "https://github.com/o/r/pull/1"}]})
    )
    decision = stop_guard.evaluate(tmp_path, stop_hook_active=False)
    assert decision.block_reason is None
    assert decision.tripped_reason is None
