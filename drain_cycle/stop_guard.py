"""Stop-hook guard for spawned drain workers.

A ``claude -p`` session that loads a "report findings / await input" skill
can end its turn before the completion sequence (commit + handoff + Linear
comment + Done) runs. The session exits cleanly with the implementation
green but uncommitted, and the orchestrator only sees a generic not-Done
halt with no signal that the work was finished.

This module is the Stop hook the worker session runs at end-of-turn. It
fires only when the orchestrator has planted ``.drain-guard.json`` in the
worktree root, so interactive sessions and non-drain runs are a silent
no-op. When the marker is present and the session is trying to stop with
work still uncaptured (dirty tree or, in stack mode, no
``.drain-handoff.json``), it returns ``decision: block`` to push the
agent through the completion sequence. After ``max_blocks`` re-injections
it gives up, writes ``.drain-guard-tripped`` with the observed state, and
lets the session exit — the orchestrator reads that marker and tags the
halt as ``worker_stopped_incomplete`` rather than the generic not-Done
line.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TextIO

from .handoff import HANDOFF_FILE, read as _read_handoff
from .worktree import BASE_FILE

MARKER_FILE = ".drain-guard.json"
TRIPPED_FILE = ".drain-guard-tripped"
DEFAULT_MAX_BLOCKS = 3
TRIPPED_HALT_REASON = "worker_stopped_incomplete"


@dataclass(frozen=True)
class GuardConfig:
    """State written by the orchestrator and updated by the hook.

    ``mode`` is ``"stack"`` (handoff-driven assembly) or ``"push"`` (direct
    push to main). ``count`` is the number of times this hook has already
    returned ``block`` for the session — incremented in place. ``max_blocks``
    is the cap before the guard gives up.
    """

    mode: str
    count: int
    max_blocks: int


def write_marker(
    worktree: Path,
    *,
    mode: Literal["stack", "push"],
    max_blocks: int = DEFAULT_MAX_BLOCKS,
) -> None:
    """Plant the per-session marker the hook keys on.

    Called by the orchestrator immediately before spawning a worker. Replaces
    any prior marker so a resumed worktree starts fresh from ``count=0``.
    Also clears any stale tripped-marker from a previous attempt so the
    orchestrator's post-session read isn't a false positive.
    """
    (worktree / MARKER_FILE).write_text(
        json.dumps({"mode": mode, "count": 0, "max_blocks": max_blocks})
    )
    tripped = worktree / TRIPPED_FILE
    if tripped.exists():
        tripped.unlink()


def read_marker(worktree: Path) -> GuardConfig | None:
    """Return the guard state for ``worktree``, or ``None`` if absent/invalid."""
    path = worktree / MARKER_FILE
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    mode = payload.get("mode")
    count = payload.get("count")
    max_blocks = payload.get("max_blocks")
    if mode not in ("stack", "push"):
        return None
    if not isinstance(count, int) or not isinstance(max_blocks, int):
        return None
    return GuardConfig(mode=mode, count=count, max_blocks=max_blocks)


def _write_marker_count(worktree: Path, config: GuardConfig, *, count: int) -> None:
    (worktree / MARKER_FILE).write_text(
        json.dumps({"mode": config.mode, "count": count, "max_blocks": config.max_blocks})
    )


def read_tripped(worktree: Path) -> str | None:
    """Return the tripped reason for ``worktree``, or ``None`` if not tripped."""
    path = worktree / TRIPPED_FILE
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


# drain-cycle's own per-session artefacts live at the worktree root and are
# expected to be untracked. ``_git_dirty`` filters them out so the guard
# doesn't see itself (or the handoff file written *after* the commit) as
# uncaptured work.
_OWN_ARTEFACTS = frozenset({MARKER_FILE, TRIPPED_FILE, HANDOFF_FILE, BASE_FILE})


def _git_dirty(worktree: Path) -> bool:
    """Return True if the worktree has uncommitted changes (excluding our own artefacts).

    ``git status --porcelain`` lines are ``XY <path>`` (two status chars,
    a space, then the path). Untracked entries for our own artefacts —
    ``.drain-guard.json``, ``.drain-guard-tripped``, ``.drain-handoff.json``
    — are skipped: the handoff is written after the commit by design, and
    the guard's own markers are not the agent's work.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(worktree),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if path in _OWN_ARTEFACTS:
            continue
        return True
    return False


def _has_handoff(worktree: Path) -> bool:
    return _read_handoff(worktree) is not None


@dataclass(frozen=True)
class Decision:
    """The hook's response to one Stop event.

    ``block_reason`` is non-None when the agent should be re-prompted (the
    hook emits ``{"decision": "block", "reason": ...}``). ``tripped_reason``
    is non-None when the guard has given up and written ``TRIPPED_FILE`` —
    the hook then exits silently and the orchestrator surfaces the tripped
    state in the halt line.
    """

    block_reason: str | None
    tripped_reason: str | None


_BLOCK_PROMPT_STACK = (
    "drain-cycle stop-guard: the issue is not finished — the worktree has "
    "uncommitted changes or no submitted PRs in .drain-handoff.json yet. "
    "Complete the remaining steps now: commit any pending changes to the "
    "issue branch as reviewable slices (do not push by hand), run "
    "`/shape:pr-finishing` to submit the stacked PR(s) — it writes the "
    "pr_urls into .drain-handoff.json and posts the review-summary comment "
    "— then transition the issue to Done. If you are genuinely blocked, "
    "leave the issue In Progress and post a comment naming the blocker — "
    "do not stop silently."
)

_BLOCK_PROMPT_PUSH = (
    "drain-cycle stop-guard: the issue is not finished — the worktree has "
    "uncommitted changes. Complete the remaining steps now: commit and push "
    "to main, post a review-summary comment on the Linear issue, then "
    "transition the issue to Done. If you are genuinely blocked, leave the "
    "issue In Progress and post a comment naming the blocker — do not stop "
    "silently."
)


def evaluate(worktree: Path, *, stop_hook_active: bool) -> Decision:
    """Decide whether to block the Stop event, give up, or pass through.

    The decision tree, in order:

    * No marker → not a drain worker session. Pass through.
    * Stack mode + clean tree + valid handoff → completion sequence ran.
      Pass through.
    * Push mode + clean tree → completion sequence ran. Pass through.
    * Otherwise: incomplete. If ``count < max_blocks`` (and we're not
      already deep in a block-loop per Claude's ``stop_hook_active`` flag),
      increment and emit a block. If at the cap, write the tripped file
      and pass through so the session can exit cleanly.

    ``stop_hook_active`` mirrors Claude's payload — when True the hook is
    being re-invoked from within a previous block, so the cap applies even
    if the count somehow lagged.
    """
    config = read_marker(worktree)
    if config is None:
        return Decision(block_reason=None, tripped_reason=None)

    dirty = _git_dirty(worktree)
    handoff_ok = _has_handoff(worktree) if config.mode == "stack" else True
    complete = (not dirty) and handoff_ok
    if complete:
        return Decision(block_reason=None, tripped_reason=None)

    state_summary = _describe_incomplete(config.mode, dirty=dirty, handoff_ok=handoff_ok)

    if config.count >= config.max_blocks or (stop_hook_active and config.count > 0):
        reason = f"{state_summary} after {config.count} re-injection(s)"
        (worktree / TRIPPED_FILE).write_text(reason)
        return Decision(block_reason=None, tripped_reason=reason)

    _write_marker_count(worktree, config, count=config.count + 1)
    prompt = _BLOCK_PROMPT_STACK if config.mode == "stack" else _BLOCK_PROMPT_PUSH
    return Decision(block_reason=prompt, tripped_reason=None)


def _describe_incomplete(mode: str, *, dirty: bool, handoff_ok: bool) -> str:
    parts = []
    if dirty:
        parts.append("uncommitted changes in worktree")
    if mode == "stack" and not handoff_ok:
        parts.append("no valid .drain-handoff.json")
    return ", ".join(parts) or "incomplete state"


def run(stdin: TextIO, stdout: TextIO, *, cwd: Path | None = None) -> int:
    """Read the Stop hook payload from ``stdin`` and emit a decision on ``stdout``.

    Exit code 0 always; the decision is carried in the JSON body. Errors
    parsing the payload fall through to a silent pass — a broken hook must
    not block a legitimate stop.
    """
    try:
        payload = json.load(stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    stop_hook_active = bool(payload.get("stop_hook_active"))
    worktree = cwd if cwd is not None else Path(payload.get("cwd") or ".").resolve()

    decision = evaluate(worktree, stop_hook_active=stop_hook_active)
    if decision.block_reason is not None:
        json.dump({"decision": "block", "reason": decision.block_reason}, stdout)
    return 0


def main() -> None:
    sys.exit(run(sys.stdin, sys.stdout))
