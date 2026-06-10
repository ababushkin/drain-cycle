"""Render-text checks for :mod:`drain_cycle.console`.

Each test captures stderr via ``capsys`` and asserts on substrings in the
rendered text — Rich strips ANSI/markup when its file isn't a TTY, so the
captured output is plain text we can grep.
"""
from __future__ import annotations

import pytest

from drain_cycle import console


def test_orch_emits_label_and_message(capsys: pytest.CaptureFixture[str]) -> None:
    console.orch("worktree ready")
    err = capsys.readouterr().err
    assert "orch" in err
    assert "worktree ready" in err


def test_worker_event_includes_identifier(capsys: pytest.CaptureFixture[str]) -> None:
    console.worker_event("ABA-42", "picked: do the thing")
    err = capsys.readouterr().err
    assert "ABA-42" in err
    assert "picked: do the thing" in err


def test_halt_line_is_verbatim(capsys: pytest.CaptureFixture[str]) -> None:
    """``halt(msg)`` must emit ``msg`` as a standalone line — the orchestrator
    contract is that this string equals the run log's ``halt_reason``."""
    msg = "Halt: ABA-1 (final state: Todo) at /tmp/x — boom"
    console.halt(msg)
    err = capsys.readouterr().err
    lines = err.splitlines()
    assert msg in lines


def test_agent_line_is_indented(capsys: pytest.CaptureFixture[str]) -> None:
    console.agent_line("hello from claude")
    err = capsys.readouterr().err
    assert "│" in err
    assert "hello from claude" in err


def test_startup_plan_renders_header_and_rows(
    capsys: pytest.CaptureFixture[str],
) -> None:
    console.startup_plan(
        "cycle-xyz",
        [
            ("ABA-1", "first thing", "sonnet-4"),
            ("ABA-2", "second thing", "opus-4"),
        ],
    )
    err = capsys.readouterr().err
    assert "drain-cycle" in err
    assert "cycle-xyz" in err
    assert "ABA-1" in err and "first thing" in err and "sonnet-4" in err
    assert "ABA-2" in err and "second thing" in err and "opus-4" in err


def test_completion_summary_clean(capsys: pytest.CaptureFixture[str]) -> None:
    console.completion_summary(
        issues_done=3,
        issues_total=3,
        halted_on=None,
        cost_usd=1.23,
        tokens=180_000,
        elapsed_seconds=125,
        run_log_path="/tmp/run.json",
    )
    err = capsys.readouterr().err
    assert "3/3" in err
    assert "$1.23" in err
    assert "180k" in err
    assert "2m" in err
    assert "/tmp/run.json" in err
    assert "Halted on" not in err


def test_completion_summary_halt_includes_reason_and_steps(
    capsys: pytest.CaptureFixture[str],
) -> None:
    console.completion_summary(
        issues_done=1,
        issues_total=4,
        halted_on="Halt: ABA-2 boom",
        cost_usd=None,
        tokens=0,
        elapsed_seconds=0,
        run_log_path="/tmp/run.json",
        next_steps=["inspect the log", "fix and re-run"],
    )
    err = capsys.readouterr().err
    assert "1/4" in err
    assert "Halt: ABA-2 boom" in err
    assert "n/a" in err
    assert "inspect the log" in err
    assert "fix and re-run" in err


def test_agent_sink_buffers_and_splits_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sink = console.AgentSink()
    sink.write("first line\nsecond ")
    sink.write("line\npartial")
    err = capsys.readouterr().err
    assert "first line" in err
    assert "second line" in err
    # partial buffered, no third line yet
    assert "partial" not in err
    sink.flush()
    err2 = capsys.readouterr().err
    assert "partial" in err2


def test_agent_sink_print_compat(capsys: pytest.CaptureFixture[str]) -> None:
    """``print(..., file=sink)`` round-trips through :func:`agent_line`."""
    sink = console.AgentSink()
    print("a non-json claude warning", file=sink)
    err = capsys.readouterr().err
    assert "a non-json claude warning" in err
