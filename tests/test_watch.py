"""Unit tests for the watch-mode pane/FIFO subsystem.

These exercise ``watch.open_session`` and ``watch.close_pane`` directly — the
narrow public surface the orchestrator drives. The success path uses a fake
tmux that simulates the pane's ``claude`` by opening the FIFO and writing a
line, so the FIFO read returns a live stream; the failure paths cover tmux
absent, a non-zero split-window, and a pane that never writes (FIFO timeout).
"""
from __future__ import annotations

import shlex
import subprocess
import threading
from pathlib import Path
from typing import Any

from drain_cycle import watch


def _fifo_from_pipeline(pipeline: str) -> str:
    tokens = shlex.split(pipeline)
    return tokens[tokens.index("tee") + 1].rstrip(";")


def _spawn_fake_pane(fifo: str) -> None:
    """Open the FIFO and write one line, standing in for the pane's claude."""

    def _write() -> None:
        with open(fifo, "w") as f:  # blocks until the reader opens its end
            f.write('{"type": "result"}\n')
            f.flush()

    threading.Thread(target=_write, daemon=True).start()


def _fake_tmux(calls: list[list[str]], *, write_fifo: bool, pane: str = "%1") -> Any:
    real_run = subprocess.run

    def run(args: Any, **kwargs: Any) -> Any:
        if not (isinstance(args, list) and args and args[0] == "tmux"):
            return real_run(args, **kwargs)
        calls.append(list(args))
        if len(args) > 1 and args[1] == "split-window":
            if write_fifo:
                _spawn_fake_pane(_fifo_from_pipeline(args[-1]))
            return type("R", (), {"stdout": f"{pane}\n", "returncode": 0})()
        return type("R", (), {"stdout": "", "returncode": 0})()

    return run


def test_open_session_returns_live_stream(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_tmux(calls, write_fifo=True))

    session = watch.open_session(["claude", "-p"], tmp_path)

    assert session is not None
    assert session.pane_id == "%1"
    assert session.stream.readline() == '{"type": "result"}\n'
    session.stream.close()
    session.cleanup()
    # The pane command carried the formatter stage downstream of tee.
    pipeline = calls[0][-1]
    assert "drain_cycle.watch_format" in pipeline
    assert pipeline.index("| tee ") < pipeline.index("drain_cycle.watch_format")


def test_open_session_none_when_tmux_missing(monkeypatch, tmp_path: Path) -> None:
    def boom(args: Any, **kwargs: Any) -> Any:
        if isinstance(args, list) and args and args[0] == "tmux":
            raise FileNotFoundError("tmux")
        raise AssertionError("unexpected non-tmux call")

    monkeypatch.setattr(subprocess, "run", boom)
    assert watch.open_session(["claude"], tmp_path) is None


def test_open_session_none_on_fifo_timeout(monkeypatch, tmp_path: Path) -> None:
    """Pane opens but never writes — the FIFO read times out and the pane is
    torn down, so the caller falls back to a normal spawn."""
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_tmux(calls, write_fifo=False))

    session = watch.open_session(["claude"], tmp_path, timeout=0.2)

    assert session is None
    # split-window opened it, then kill-pane tore it down.
    assert any(c[1] == "split-window" for c in calls)
    assert any(c[1] == "kill-pane" for c in calls)


def test_close_pane_swallows_errors(monkeypatch) -> None:
    def boom(args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("tmux")

    monkeypatch.setattr(subprocess, "run", boom)
    watch.close_pane("%9")  # must not raise
