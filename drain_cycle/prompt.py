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


_TAIL = (
    "before marking Done: run /code-review-and-quality on the working-tree "
    "changes, fix Critical/Required findings, commit + push, then post a "
    "review-summary comment on the issue and transition to Done."
)

_STACK_TAIL = (
    "before finishing: run /code-review-and-quality on the working-tree "
    "changes, fix Critical/Required findings, commit to the issue branch "
    "without pushing, then write .drain-handoff.json with the PR body and "
    "review findings."
)


def _resume_directive(identifier: str) -> str:
    """Resume preamble for a worktree carrying prior committed work.

    Inserted as the first line inside the preamble (after the ``---``
    separator, before "Execution instructions:") so the agent reads it
    ahead of the procedure but the tail still holds the last-line
    position the four-segment ordering reserves for it.
    """
    return (
        f"Resuming issue {identifier}: this worktree carries prior committed "
        "work from an earlier session that was halted. Run "
        "`git log --oneline main..HEAD` and `git status` first to read what "
        "is already done, then continue from that point — do not restart "
        "from scratch.\n\n"
    )


def _normal_preamble(identifier: str, worktree: Path, resume_segment: str) -> str:
    return (
        "---\n\n"
        f"{resume_segment}"
        "Execution instructions:\n"
        f"- Working directory: {worktree}\n"
        "- Base branch: main\n"
        f"- Completion sequence for issue {identifier} (run in this order, "
        "before marking Done):\n"
        "  1. Run `/code-review-and-quality` against the working-tree changes.\n"
        "  2. Fix any Critical or Required findings. Lower-severity findings "
        "are at your discretion.\n"
        "  3. Commit and push to main.\n"
        "  4. Post a short review-summary comment on the Linear issue via "
        "`mcp__claude_ai_Linear__save_comment` (count of findings by severity, "
        "fixed vs deferred).\n"
        "  5. Transition issue to Done via `mcp__claude_ai_Linear__save_issue` "
        '(state: "Done").\n'
    )


def _stack_preamble(identifier: str, worktree: Path, resume_segment: str) -> str:
    return (
        "---\n\n"
        f"{resume_segment}"
        "Execution instructions:\n"
        f"- Working directory: {worktree}\n"
        "- Base branch: main\n"
        f"- Completion sequence for issue {identifier} (run in this order):\n"
        "  1. Run `/code-review-and-quality` against the working-tree changes.\n"
        "  2. Fix any Critical or Required findings. Lower-severity findings "
        "are at your discretion.\n"
        "  3. Commit to the issue branch (do not push).\n"
        "  4. Write `.drain-handoff.json` in the worktree root with keys:\n"
        "     - `pr_title`: a concise PR title (≤ 70 characters)\n"
        "     - `pr_body`: markdown with ## What, ## Why, and ## What to review "
        "sections\n"
        '     - `findings`: `{"critical": N, "required": N}` counts from the '
        "review\n"
    )


def build(
    issue: dict[str, Any],
    worktree: Path,
    *,
    resumed: bool = False,
    stack: bool = False,
) -> str:
    title = issue.get("title", "")
    description = issue.get("description") or ""
    identifier = issue.get("identifier", "")

    resume_segment = _resume_directive(identifier) if resumed else ""

    if stack:
        preamble = _stack_preamble(identifier, worktree, resume_segment)
        tail = _STACK_TAIL
    else:
        preamble = _normal_preamble(identifier, worktree, resume_segment)
        tail = _TAIL

    return (
        f"# {title}\n\n"
        f"{description}\n\n"
        f"{preamble}\n"
        f"{tail}\n"
    )
