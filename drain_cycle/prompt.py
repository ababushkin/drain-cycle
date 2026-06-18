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

_FINISHING_OPUS_MODEL = "claude-opus-4-7"


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


def build_finishing(
    identifier: str, worktree: Path, base: str, *, stack: bool = True
) -> str:
    """Build a finishing-only prompt for a committed-but-unfinished issue.

    The implementation is already committed. The agent runs review → fix →
    pr-finishing → records the submission. It must not re-implement or add
    commits beyond those needed to fix Critical/Required review findings.
    Critical/Required fixes are delegated to ``_FINISHING_OPUS_MODEL`` sub-agents.

    The completion step is mode-dependent. In ``stack`` mode the submitted PR is
    the completion proof and the issue stays In Progress until that PR merges —
    the agent must not transition to Done. In push mode there is no PR to merge,
    so the push to main is the completion proof and the agent marks the issue
    Done.
    """
    if base == "main":
        base_clause = ""
    else:
        base_clause = (
            f" These commits are stacked on `{base}`, not `main`, so pass "
            f"`{base}` to the skill as its base branch (it slices `{base}..HEAD`)."
        )
    if stack:
        finish_step = (
            "  5. Confirm `exec-state.json` now contains a non-empty `pr_urls` "
            "list — that submitted PR is the completion signal. Leave the issue "
            "In Progress: do not transition it to Done; it stays In Progress "
            "until the PR merges. If the skill could not submit, leave the issue "
            "In Progress and comment the blocker.\n\n"
            "before finishing: confirm the PR URLs are in `exec-state.json` and "
            "leave the issue In Progress.\n"
        )
        steps_lead = "- Steps (run in order):\n"
    else:
        finish_step = (
            "  5. Confirm `exec-state.json` now contains a non-empty `pr_urls` "
            "list. If the skill could not submit, leave the issue In Progress and "
            "comment the blocker — do not mark Done.\n"
            "  6. Transition issue to Done via `mcp__claude_ai_Linear__save_issue` "
            '(state: "Done").\n\n'
            "before marking Done: confirm the PR URLs are in `exec-state.json` "
            "and transition to Done.\n"
        )
        steps_lead = "- Steps (run in order, before marking Done):\n"
    return (
        f"# Finishing incomplete issue {identifier}\n\n"
        f"The implementation for this issue is already committed on this branch. "
        f"Run `git log --oneline {base}..HEAD` to see the committed work.\n\n"
        "Your only task is to run the finishing protocol below. Do not "
        "re-implement, redesign, or add commits beyond those needed to fix "
        "Critical/Required review findings.\n\n"
        "---\n\n"
        f"Finishing instructions for issue {identifier}:\n"
        f"- Working directory: {worktree}\n"
        f"- Base branch: {base}\n"
        f"{steps_lead}"
        "  1. Review the committed changes for correctness and quality.\n"
        "  2. Fix any Critical or Required findings. For each fix that needs "
        f"significant code changes, spawn a sub-agent on `{_FINISHING_OPUS_MODEL}` "
        "with the specific fix task — do not switch models for the overall session.\n"
        "  3. Commit any review-fix changes to the branch as reviewable slices "
        "(if any). Do not push by hand.\n"
        f"  4. Run `/shape:pr-finishing`.{base_clause} It submits the slices as "
        "stacked PR(s) via Graphite, writes the submitted PR URLs into "
        "`exec-state.json` (`pr_urls`), and posts the review-summary comment "
        "on the Linear issue. Do not run `gt`/`gh` by hand or write "
        "`exec-state.json` yourself — the skill owns both.\n"
        f"{finish_step}"
    )
