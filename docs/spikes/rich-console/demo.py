"""Render a representative drain-cycle session via :mod:`drain_cycle.console`.

Run from the repo root::

    uv run python docs/spikes/rich-console/demo.py

Used to validate the visual hierarchy of startup plan → labeled runtime events
(``orch`` / ``ABA-NNN`` / ``HALT``) → indented agent output → completion
summary, without spawning real ``claude`` sessions or touching Linear.
"""
from __future__ import annotations

import time

from drain_cycle import console


def main() -> None:
    console.startup_plan(
        "cycle-2026-Q2-w3",
        [
            ("ABA-300", "spike: rich console event log", "claude-sonnet-4-6"),
            ("ABA-301", "wire console.py into orchestrator.py", "claude-sonnet-4-6"),
            ("ABA-302", "route agent output through agent_line()", "claude-haiku-4-5"),
        ],
    )

    console.orch("worktrees ready for cycle (3 issues)")
    console.worker_event("ABA-300", "picked: spike: rich console event log")
    sink = console.AgentSink()
    sink.write("starting session (model=claude-sonnet-4-6)\n")
    sink.write("reading docs/design-decisions.md ...\n")
    console.worker_event("ABA-300", "turn 4 · 22k tok (peak 22k) · 14s")
    sink.write("running pytest -x -q\n")
    console.worker_event("ABA-300", "done; PR https://github.com/example/repo/pull/42")

    console.worker_event("ABA-301", "picked: wire console.py into orchestrator.py")
    console.halt(
        "Halt: ABA-301 (final state: In Progress) at "
        "/Users/anton/src/drain-cycle/.worktrees/ABA-301 — per-issue time cap reached (3600s)"
    )

    time.sleep(0.05)
    console.completion_summary(
        issues_done=1,
        issues_total=3,
        halted_on="Halt: ABA-301 per-issue time cap reached (3600s)",
        cost_usd=2.41,
        tokens=380_000,
        elapsed_seconds=4280,
        run_log_path="~/.drain-cycle/runs/cycle-2026-Q2-w3-20260610T100000Z.json",
        next_steps=[
            "inspect the run log for halt context",
            "raise per_issue_seconds in limits.yml or split the work",
            "re-run drain-cycle to resume",
        ],
    )


if __name__ == "__main__":
    main()
