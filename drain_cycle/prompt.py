"""Prompt builder for spawned ``claude -p`` sessions.

The prompt is the entire contract between the orchestrator and the spawned
agent — there's no system prompt, no multi-turn loop. The four-segment
ordering below is load-bearing: the agent reads top-down, so context
(title + body) comes before instructions (preamble + tail), and the tail
line is last so it stays in the trailing-tokens window the model attends
to most strongly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _resume_directive(identifier: str, base: str) -> str:
    """Resume preamble for a worktree carrying prior committed work.

    Inserted as the first line inside the preamble (after the ``---``
    separator, before "Execution instructions:") so the agent reads it
    ahead of the pointer but the tail still holds the last-line
    position the four-segment ordering reserves for it.

    The ``git log`` range is anchored at ``base`` so a chained worktree
    (branched off the prior issue's branch, not ``main``) reads back only
    its own commits rather than the whole stack beneath it.
    """
    return (
        f"Resuming issue {identifier}: this worktree carries prior committed "
        "work from an earlier session that was halted. Run "
        f"`git log --oneline {base}..HEAD` and `git status` first to read what "
        "is already done, then continue from that point — do not restart "
        "from scratch.\n\n"
    )


def _preamble(identifier: str, worktree: Path, base: str, resume_segment: str) -> str:
    return (
        "---\n\n"
        f"{resume_segment}"
        "Execution instructions:\n"
        f"- Working directory: {worktree}\n"
        f"- Base branch: {base}\n"
        "\n"
        "Run `/shape:exec:pickup` to execute this issue end-to-end.\n"
    )


_TAIL = "before marking Done: run `/shape:exec:pickup`."


def build(
    issue: dict[str, Any],
    worktree: Path,
    *,
    resumed: bool = False,
    base: str = "main",
) -> str:
    title = issue.get("title", "")
    description = issue.get("description") or ""
    identifier = issue.get("identifier", "")

    resume_segment = _resume_directive(identifier, base) if resumed else ""
    preamble = _preamble(identifier, worktree, base, resume_segment)

    return (
        f"# {title}\n\n"
        f"{description}\n\n"
        f"{preamble}\n"
        f"{_TAIL}\n"
    )
