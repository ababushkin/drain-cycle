"""Read the per-issue handoff file written by the spawned agent.

The agent writes ``.drain-cycle-handoff.json`` into its worktree root after
completing its code review, recording the count of findings at each severity
level. The orchestrator reads this before assembling the stack so it can flag
PRs that warrant closer attention.

A missing or malformed file returns default ``Findings`` (all zeros) — safe
because the ``review:high`` Linear label is the primary flag signal and
findings are secondary context.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

HANDOFF_FILENAME = ".drain-cycle-handoff.json"


@dataclass(frozen=True)
class Findings:
    critical: int = 0
    required: int = 0


def read(worktree_path: Path) -> Findings:
    """Return findings from the agent's handoff file, defaulting to zeros."""
    path = worktree_path / HANDOFF_FILENAME
    try:
        data = json.loads(path.read_text())
        review = data.get("findings") or {}
        return Findings(
            critical=int(review.get("critical", 0)),
            required=int(review.get("required", 0)),
        )
    except (AttributeError, OSError, json.JSONDecodeError, ValueError, TypeError):
        return Findings()
