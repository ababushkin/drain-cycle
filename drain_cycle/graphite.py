"""Graphite + GitHub CLI primitives for assembling a per-repo PR stack.

Commands run from the per-issue worktree root (``cwd=worktree``), where ``gt``
reads the shared ``.git/.graphite_metadata.db``. Telemetry spans mirror
``worktree.py``: ``drain.graphite.*`` span names, capture-stderr →
``RuntimeError`` pattern.

Per-repo preconditions (one-time, operator-owned, not automatable under
``--dangerously-skip-permissions``):

  gt auth             # interactive browser flow
  gt init --trunk main

If either is absent, ``gt submit`` aborts before any push and the
``RuntimeError`` propagates to the orchestrator's stop-the-line halt.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import telemetry

_REVIEW_HIGH_COLOR = "B60205"


@dataclass(frozen=True)
class PrInfo:
    url: str
    number: int


def submit(worktree: Path, *, parent: str, title: str = "", body: str = "") -> PrInfo:
    """Track the branch in Graphite and submit the full stack.

    Runs ``gt track --parent <parent>``, ``gt submit --stack
    --no-interactive --publish``, then fetches the current branch's PR
    info via ``gh pr view``. A non-empty ``title``/``body`` (from the
    agent's handoff file) is written onto the PR via ``gh pr edit``.
    Raises ``RuntimeError`` (capturing stderr) on any non-zero exit —
    callers treat this as a stop-the-line halt.
    """
    with telemetry.tracer.start_as_current_span("drain.graphite.submit") as span:
        span.set_attribute("graphite.parent", parent)
        span.set_attribute("graphite.worktree", str(worktree))
        _run(["gt", "track", "--parent", parent], cwd=worktree)
        _run(["gt", "submit", "--stack", "--no-interactive", "--publish"], cwd=worktree)
        raw = _run_output(["gh", "pr", "view", "--json", "number,url"], cwd=worktree)
        try:
            data = json.loads(raw)
            info = PrInfo(url=data["url"], number=data["number"])
        except (json.JSONDecodeError, KeyError) as exc:
            raise RuntimeError(
                f"gh pr view returned unexpected output: {exc!r}"
            ) from exc
        if title or body:
            edit_args = ["gh", "pr", "edit", str(info.number)]
            if title:
                edit_args += ["--title", title]
            if body:
                edit_args += ["--body", body]
            _run(edit_args, cwd=worktree)
        span.set_attribute("graphite.pr_number", info.number)
        span.set_attribute("graphite.pr_url", info.url)
        return info


def ensure_review_high_label(worktree: Path) -> None:
    """Create the ``review:high`` GitHub label if absent (idempotent via ``--force``)."""
    with telemetry.tracer.start_as_current_span("drain.graphite.ensure_label"):
        _run(
            ["gh", "label", "create", "review:high",
             "--color", _REVIEW_HIGH_COLOR, "--force"],
            cwd=worktree,
        )


def add_label(pr_number: int, label: str, worktree: Path) -> None:
    """Add a label to the specified GitHub PR."""
    with telemetry.tracer.start_as_current_span("drain.graphite.add_label") as span:
        span.set_attribute("graphite.pr_number", pr_number)
        span.set_attribute("graphite.label", label)
        _run(["gh", "pr", "edit", str(pr_number), "--add-label", label], cwd=worktree)


def _run(args: list[str], *, cwd: Path) -> None:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip() or '<no output>'}"
        )


def _run_output(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip() or '<no output>'}"
        )
    return result.stdout
