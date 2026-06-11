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

_VERIFY_FLOW_LABEL = "verify"


def is_verify_flow(issue: dict[str, Any]) -> bool:
    """Return True when the issue carries the ``verify`` label."""
    return _VERIFY_FLOW_LABEL in issue.get("labels", [])


def _shape_task_directive(identifier: str) -> str:
    """Shape-task preamble for verify-flow worker sessions.

    Instructs the worker to check for an existing Task Shaper output
    block in the Linear issue body before invoking ``/shape:task``. The
    check-first approach handles both fresh sessions (block absent →
    invoke) and §14 resumes (block present → reuse).
    """
    return (
        f"Shaping step (run before implementing): fetch the Linear issue body "
        f"for {identifier} and check for a Task Shaper output block "
        "(delimited by `<!-- TASK-SHAPER-START -->` and `<!-- TASK-SHAPER-END -->`). "
        "If the block is present, use it as your implementation guide and skip "
        "re-running `/shape:task`. If the block is absent, run "
        f"`/shape:task {identifier}` first and use its output as your "
        "implementation guide.\n\n"
    )


_TAIL = (
    "before marking Done: review the working-tree changes for correctness "
    "and quality, fix Critical/Required findings, commit + push, then post a "
    "review-summary comment on the issue and transition to Done."
)

_STACK_TAIL = (
    "before marking Done: review the changes, fix Critical/Required findings, "
    "commit them as reviewable slices on the issue branch, then run "
    "`/shape:pr-finishing` to submit the stacked PR(s) and post the "
    "review-summary comment, and only then transition to Done."
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


def _normal_preamble(
    identifier: str, worktree: Path, resume_segment: str, shape_task_segment: str
) -> str:
    return (
        "---\n\n"
        f"{resume_segment}"
        f"{shape_task_segment}"
        "Execution instructions:\n"
        f"- Working directory: {worktree}\n"
        "- Base branch: main\n"
        f"- Completion sequence for issue {identifier} (run in this order, "
        "before marking Done):\n"
        "  1. Review the working-tree changes for correctness and quality.\n"
        "  2. Fix any Critical or Required findings. Lower-severity findings "
        "are at your discretion.\n"
        "  3. Commit and push to main.\n"
        "  4. Post a short review-summary comment on the Linear issue via "
        "`mcp__claude_ai_Linear__save_comment` (count of findings by severity, "
        "fixed vs deferred).\n"
        "  5. Transition issue to Done via `mcp__claude_ai_Linear__save_issue` "
        '(state: "Done").\n'
    )


def _stack_preamble(
    identifier: str, worktree: Path, resume_segment: str, shape_task_segment: str
) -> str:
    return (
        "---\n\n"
        f"{resume_segment}"
        f"{shape_task_segment}"
        "Execution instructions:\n"
        f"- Working directory: {worktree}\n"
        "- Base branch: main\n"
        f"- Completion sequence for issue {identifier} (run in this order, "
        "before marking Done):\n"
        "  1. Review the working-tree changes for correctness and quality.\n"
        "  2. Fix any Critical or Required findings. Lower-severity findings "
        "are at your discretion.\n"
        "  3. Commit the changes to the issue branch as reviewable slices — "
        "one logical change per commit. Do not push by hand.\n"
        "  4. Run `/shape:pr-finishing`. It submits the slices as stacked "
        "PR(s) via Graphite, writes the submitted PR URLs into "
        "`.drain-handoff.json` (`pr_urls`), and posts the review-summary "
        "comment on the Linear issue. Do not run `gt`/`gh` by hand or write "
        "`.drain-handoff.json` yourself — the skill owns both.\n"
        "  5. Confirm `.drain-handoff.json` now contains a non-empty `pr_urls` "
        "list. If the skill could not submit, leave the issue In Progress and "
        "comment the blocker — do not mark Done.\n"
        "  6. Transition issue to Done via `mcp__claude_ai_Linear__save_issue` "
        '(state: "Done").\n'
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
    shape_task_segment = _shape_task_directive(identifier) if is_verify_flow(issue) else ""

    if stack:
        preamble = _stack_preamble(identifier, worktree, resume_segment, shape_task_segment)
        tail = _STACK_TAIL
    else:
        preamble = _normal_preamble(identifier, worktree, resume_segment, shape_task_segment)
        tail = _TAIL

    return (
        f"# {title}\n\n"
        f"{description}\n\n"
        f"{preamble}\n"
        f"{tail}\n"
    )
