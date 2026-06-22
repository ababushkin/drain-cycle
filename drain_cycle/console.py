"""Structured Rich event log for the drain-cycle orchestrator.

Replaces the scattered ``print(..., file=sys.stderr)`` calls with a small set of
labeled, timestamped event functions plus startup and completion tables. Every
function writes to ``sys.stderr`` so stdout stays free for piping.

The three event labels are:

* ``orch``     — orchestrator-side activity (worktree setup, spawn, PR posts,
  non-fatal errors).
* ``ABA-NNN``  — per-issue outcomes (picked, done, progress).
* ``HALT``     — halt conditions; rendered red.

Agent output (the worker's ``passthrough`` stream) is indented with ``│`` via
:class:`AgentSink` so it is visually distinct from orchestrator lines.

The Rich ``Console`` is built lazily on each call against the live
``sys.stderr`` reference. That keeps pytest's ``capsys`` capture working — it
swaps ``sys.stderr`` after this module is imported, and a cached Console
would otherwise still hold the original.
"""
from __future__ import annotations

import sys
from datetime import datetime
from typing import Iterable, Sequence

from rich.console import Console
from rich.table import Table


def _console() -> Console:
    return Console(file=sys.stderr, highlight=False, soft_wrap=True)


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _line(label: str, label_style: str, message: str) -> None:
    _console().print(
        f"[dim]{_ts()}[/dim] [{label_style}]{label}[/{label_style}]  {message}"
    )


def orch(message: str) -> None:
    """Emit an orchestrator-side event."""
    _line("orch", "cyan", message)


def worker_event(identifier: str, message: str) -> None:
    """Emit a per-issue event tagged with ``identifier``."""
    _line(identifier, "magenta", message)


def halt(message: str) -> None:
    """Emit a halt event.

    The line is rendered verbatim (red on a TTY, plain text otherwise) so it
    matches the value the orchestrator wrote into the run log's
    ``halt_reason`` field — a contract relied on by halt-grep tooling and the
    operator's eye.
    """
    _console().print(f"[bold red]{message}[/bold red]", markup=True, highlight=False)


def agent_line(line: str) -> None:
    """Emit a single line of agent output, indented with ``│``."""
    _console().print(f"[dim]│[/dim] {line}", highlight=False)


def startup_plan(
    target_id: str,
    rows: Sequence[tuple[str, str, str]],
    *,
    target_kind: str = "cycle",
) -> None:
    """Print the startup header (Rich rule) and a plan table.

    ``rows`` is a sequence of ``(identifier, title, model)`` triples in the
    order the orchestrator will execute them. ``target_kind`` labels the
    drain target — ``"cycle"`` (the default) or ``"project"`` — and appears
    verbatim in the rule alongside ``target_id``.
    """
    c = _console()
    c.rule(f"[bold]drain-cycle[/bold]  {target_kind} {target_id}")
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Issue", no_wrap=True, width=10)
    table.add_column("Title", overflow="fold")
    table.add_column("Model", no_wrap=True)
    for identifier, title, model_id in rows:
        table.add_row(identifier, title, model_id)
    c.print(table)


def completion_summary(
    *,
    issues_done: int,
    issues_total: int,
    halted_on: str | None,
    cost_usd: float | None,
    tokens: int,
    elapsed_seconds: float,
    run_log_path: str,
    next_steps: Iterable[str] = (),
) -> None:
    """Print a summary block at the end of a drain (clean or halt)."""
    from .progress import fmt_elapsed, fmt_tokens

    c = _console()
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column("k", style="bold")
    table.add_column("v")
    table.add_row("Issues done", f"{issues_done}/{issues_total}")
    if halted_on is not None:
        table.add_row("Halted on", halted_on)
    table.add_row("Cost", "n/a" if cost_usd is None else f"${cost_usd:.2f}")
    table.add_row("Tokens", fmt_tokens(tokens))
    table.add_row("Elapsed", fmt_elapsed(elapsed_seconds))
    table.add_row("Run log", run_log_path)
    c.rule()
    c.print(table)
    for step in next_steps:
        c.print(f"  [dim]→[/dim] {step}")


class AgentSink:
    """File-like wrapper that routes ``print``/``write`` output through
    :func:`agent_line`, line-buffered.

    The orchestrator hands an instance to ``worker.run_issue`` via its
    ``passthrough`` parameter so agent non-JSON output flows through the same
    Rich console as orchestrator events, with the ``│`` indent prefix. Tests
    that need to assert on raw agent output continue to pass an ``io.StringIO``
    instead.
    """

    def __init__(self) -> None:
        self._buf = ""

    def write(self, s: str) -> int:
        if not s:
            return 0
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                agent_line(line)
        return len(s)

    def flush(self) -> None:
        if self._buf:
            agent_line(self._buf)
            self._buf = ""
