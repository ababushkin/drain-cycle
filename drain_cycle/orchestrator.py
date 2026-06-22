"""Drain a cycle by iterating over its sorted Todo/Backlog issues.

Halt-on-not-Done, the orchestrator-owned Todo→In-Progress transition, the
run-log artefact, and the inspectable-halt UX all live here. The halt-message
helper ``_halt_message`` is the single source of truth for the operator-facing
halt string — emitted both on stderr and into the run-log entry's
``halt_reason`` field.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field, replace as dataclass_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO

from opentelemetry.trace import Span

from . import console, handoff, linear, model, progress, prompt, runlog, stop_guard, swimlanes, telemetry, worker, worktree
from . import watch as watch_pane
from .limits import Limits, check_cycle
from .linear import DependencyCycleError
from .repos import RepoResolutionError, Repos

_DONE_STATE_TYPE = "completed"
_IN_PROGRESS_STATE_NAME = "In Progress"
_CLAUDE_CMD = ["claude", "-p", "--dangerously-skip-permissions"]
_DEBUG_ENV_VAR = "DRAIN_CYCLE_DEBUG"
"""Opt-in switch for per-issue ``--debug-file`` capture. Any non-empty value
turns it on; the worker then writes each session's startup diagnostics
(settings sources, plugins, MCP servers, hooks) beside the run log. Off by
default — the diagnostic exists for one-shot investigation, not steady state.
See ``docs/adrs/0014-worktree-config-symlink.md``."""
_UNRESOLVED_WORKTREE_DISPLAY = "<unresolved>"
"""Worktree-path placeholder for the pre-spawn resolution-halt path.
No path has been chosen yet — the issue couldn't be mapped to a target
repo — so the run-log entry and stderr halt line carry this marker
rather than a misleading fake path."""

_FINISHING_MODEL = "claude-sonnet-4-6"
"""Model for the finishing sub-agent spawned to recover a committed-but-unfinished
issue. Fixed at sonnet regardless of the original issue's model: label so the
mechanical protocol (review → fix → pr-finishing → Done) runs reliably."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _commits_beyond_base(worktree_path: Path, base: str) -> bool:
    """Return True if ``base..HEAD`` contains at least one commit.

    Verifies that ``base`` is an ancestor of HEAD before counting —
    an ambiguous base (not an ancestor) returns False so the caller
    skips recovery rather than making a wrong decision.
    Returns False on any subprocess error so failure-to-check is
    treated as "no commits to work from", which is the safe direction.
    """
    try:
        ancestor_check = subprocess.run(
            ["git", "merge-base", "--is-ancestor", base, "HEAD"],
            cwd=str(worktree_path),
            capture_output=True,
            timeout=10,
        )
        if ancestor_check.returncode != 0:
            return False
        count_result = subprocess.run(
            ["git", "rev-list", "--count", f"{base}..HEAD"],
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return count_result.returncode == 0 and int(count_result.stdout.strip()) > 0
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return False


def _emit_summary(log: runlog.RunLog, *, total: int, halted_on: str | None) -> None:
    done = sum(1 for e in log.entries if e.get("final_linear_state") == "Done")
    next_steps = [] if halted_on is None else [
        "inspect the run log for halt context",
        "fix and re-run drain-cycle to resume",
    ]
    console.completion_summary(
        issues_done=done,
        issues_total=total,
        halted_on=halted_on,
        cost_usd=log.cycle_cost_usd() or None,
        tokens=log.cycle_tokens_cumulative(),
        elapsed_seconds=log.cycle_duration_seconds(),
        run_log_path=str(log.path),
        next_steps=next_steps,
    )


def _halt_message(identifier: str, state_name: str, worktree_path: Path) -> str:
    """Single source of truth for the halt UX.

    The same string lands on stderr (the operator's grep anchor) and in
    the run-log entry's ``halt_reason`` field — so kill-condition tooling
    reads the same human-readable explanation the operator saw at halt
    time.
    """
    return f"Halt: {identifier} (final state: {state_name}) at {worktree_path}"


def _resume_attempts(cycle_id: str, identifier: str) -> int:
    """Count prior halted attempts for ``identifier`` across this cycle's run logs.

    Globs ``~/.drain-cycle/runs/<cycle_id>-*.json`` and tallies entries
    whose ``issue_identifier`` matches and whose ``final_linear_state``
    is not ``"Done"`` — the same shape every halt path writes. The
    orchestrator compares this against ``limits.max_resume_attempts``
    before spawning, so a perma-stuck issue stops consuming attempts
    once its budget is spent.

    Unreadable, unparseable, or shape-corrupted log files are skipped
    rather than failed: a partial-write file from a SIGKILL'd earlier
    run, or a file whose ``entries`` is missing/null/non-list, must
    not pin the operator out of running the cycle. The shape checks
    are isinstance-guarded so a JSON file that parses but doesn't
    match :class:`RunLog`'s on-disk schema is treated as opaque rather
    than crashing the helper.
    """
    count = 0
    for path in runlog.runs_dir().glob(f"{cycle_id}-*.json"):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        entries = payload.get("entries")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if (
                entry.get("issue_identifier") == identifier
                and entry.get("final_linear_state") != "Done"
            ):
                count += 1
    return count


def _revert_to_pre_halt_state(
    issue_id: str, *, target_state_name: str, pre_revert_state_name: str
) -> tuple[str, str | None]:
    """Restore Linear state on halt; return ``(state_to_report, error_msg)``.

    The orchestrator transitions issues Todo→In Progress before spawning the
    agent. When the run halts, that In-Progress flag leaves the issue outside
    ``_PENDING_STATE_TYPES`` so a re-run silently skips it. This helper
    reverses the transition and re-fetches to confirm.

    On revert success, returns the refreshed state name and ``None``.
    On revert failure, returns ``pre_revert_state_name`` (the state the
    issue is actually still in, so the operator can find it) plus the
    exception message — non-fatal. A failed refresh after a
    successful revert falls back to ``target_state_name``, since we trust
    the mutation landed even if the read-back didn't.
    """
    try:
        linear.set_state(issue_id, target_state_name)
    except Exception as exc:
        return pre_revert_state_name, str(exc)
    try:
        refreshed = linear.get_issue(issue_id)
    except Exception:
        return target_state_name, None
    return refreshed["state"]["name"], None


def _worker_log_fields(result: worker.WorkerResult) -> dict[str, object]:
    """Map a ``WorkerResult`` onto the run-log entry's usage fields.

    Shared by all three worker-backed ``append_entry`` calls (timeout
    halt, Done, not-Done halt) so the recorded usage shape can't drift
    between branches.
    """
    return {
        "duration_seconds": result.duration_seconds,
        "model": result.model,
        "usage": result.usage,
        "cost_usd": result.cost_usd,
        "num_turns": result.num_turns,
        "session_id": result.session_id,
        "is_error": result.is_error,
    }


def _log_finishing_done(identifier: str, result: worker.WorkerResult) -> None:
    """Emit a completion line for a finishing sub-agent.

    The finishing agent runs off the watch pane, so without an explicit
    end-of-run line the orchestrator pane would fall silent when it exits —
    the same blind spot that makes an in-flight finishing run look hung. The
    line mirrors the worker's ``=== done ===`` frame: turns and cost, flagged
    if the session errored.
    """
    cost = "n/a" if result.cost_usd is None else f"${result.cost_usd:.2f}"
    suffix = " (error)" if result.is_error else ""
    console.worker_event(
        identifier,
        f"finishing sub-agent done: {result.num_turns} turns, {cost}{suffix}",
    )


@dataclass(frozen=True)
class _WorkerOutcome:
    """The post-spawn verdict/responder trio plus the worker result, gathered
    once after the worker exits and passed as a unit to the halt epilogue and
    the span-attribute writer so the recorded shape can't drift across branches.
    """

    result: worker.WorkerResult
    outcome_verdict: dict | None = None
    prep_verdict: dict | None = None
    review_verdict: dict | None = None
    responder_runs: list[dict] = field(default_factory=list)


def _set_verdict_span_attrs(
    issue_span: Span,
    outcome: _WorkerOutcome,
) -> None:
    """Record the verdict/responder span attributes shared by the worker-backed
    halt branches and the Done append, so the recorded shape can't drift."""
    outcome_result = (outcome.outcome_verdict or {}).get("result")
    if outcome_result is not None:
        issue_span.set_attribute("issue.outcome_verdict", outcome_result)
    prep_result = (outcome.prep_verdict or {}).get("result")
    if prep_result is not None:
        issue_span.set_attribute("issue.prep_verdict", prep_result)
    review_result = (outcome.review_verdict or {}).get("result")
    if review_result is not None:
        issue_span.set_attribute("issue.review_verdict", review_result)


@dataclass(frozen=True)
class _HaltContext:
    """Per-issue constants for recording a terminal halt.

    Every halt branch builds its own ``halt_reason`` and any branch-specific
    span attributes, then funnels through :meth:`record` for the universal
    epilogue: the run-log entry, the ``final_linear_state`` span attribute, the
    error mark, and the operator-facing halt line. Pass ``outcome`` on the
    post-spawn paths to splice the recorded usage and verdict fields into the
    entry; the pre-spawn paths leave it ``None``.
    """

    log: runlog.RunLog
    issue_span: Span
    identifier: str
    started_at: str

    def record(
        self,
        *,
        slug: str,
        halt_reason: str,
        final_linear_state: str,
        worktree_path: str,
        finished_at: str,
        exit_code: int,
        outcome: _WorkerOutcome | None = None,
        finishing_runs: list[dict] | None = None,
    ) -> None:
        entry_fields: dict[str, object] = {}
        if outcome is not None:
            entry_fields = {
                "outcome_verdict": outcome.outcome_verdict,
                "prep_verdict": outcome.prep_verdict,
                "review_verdict": outcome.review_verdict,
                "responder_runs": outcome.responder_runs,
                **_worker_log_fields(outcome.result),
            }
        self.log.append_entry(
            issue_identifier=self.identifier,
            started_at=self.started_at,
            finished_at=finished_at,
            exit_code=exit_code,
            final_linear_state=final_linear_state,
            worktree_path=worktree_path,
            halt_reason=halt_reason,
            finishing_runs=finishing_runs,
            **entry_fields,
        )
        self.issue_span.set_attribute("issue.final_linear_state", final_linear_state)
        telemetry.mark_error(self.issue_span, slug, halt_reason)
        console.halt(halt_reason)


def _debug_enabled() -> bool:
    """Whether per-issue ``--debug-file`` capture is switched on.

    Read from the environment (``os.environ`` already carries any
    ``~/.drain-cycle/.env`` value loaded at CLI startup), so the operator
    turns it on for one investigative run with ``DRAIN_CYCLE_DEBUG=1
    drain-cycle`` and leaves it off otherwise.
    """
    return bool(os.environ.get(_DEBUG_ENV_VAR))


def run(
    repos: Repos,
    limits: Limits | None = None,
    *,
    watch: bool = False,
    no_stack: bool = False,
    project: str | None = None,
) -> int:
    """Drain the current cycle inside the ``drain.cycle`` root span.

    The span wrapper is thin so the body keeps its shape; per-issue work nests
    under it via ``_drain_one_issue``'s ``drain.issue`` spans, and the Linear,
    worktree, and worker spans nest under those — yielding one trace per drain.

    When ``project`` is given (a name or UUID), the drain runs over that Linear
    project's pending issues instead of the active cycle. The same ``cycle_id``
    local carries the resolved project id through every downstream consumer —
    run-log filename, resume-glob, progress marker, telemetry — overloading the
    identity field per ADR 0033.
    """
    if limits is None:
        limits = Limits()
    with telemetry.tracer.start_as_current_span("drain.cycle") as cycle_span:
        return _run(
            repos, limits, cycle_span,
            watch=watch, no_stack=no_stack, project=project,
        )


def _run(
    repos: Repos,
    limits: Limits,
    cycle_span: Span,
    *,
    watch: bool = False,
    no_stack: bool = False,
    project: str | None = None,
) -> int:
    debug = _debug_enabled()
    if project is not None:
        cycle_id = linear.resolve_project_id(project)
    else:
        cycle_id = linear.current_cycle_id()
    cycle_span.set_attribute("drain.cycle_id", cycle_id)
    log = runlog.RunLog(cycle_id=cycle_id)
    try:
        if project is not None:
            plan = linear.project_issues(cycle_id)
        else:
            plan = linear.pending_issues(cycle_id)
    except DependencyCycleError as exc:
        halt_reason = f"Halt: {exc}"
        log.set_cycle_halt(halt_reason)
        telemetry.mark_error(cycle_span, "err-dependency-cycle", halt_reason)
        console.halt(halt_reason)
        _emit_summary(log, total=0, halted_on=halt_reason)
        return 1

    cycle_span.set_attribute("drain.issues_planned", len(plan.order))
    cycle_span.set_attribute("drain.issues_deferred", len(plan.deferred))

    if not plan.order and not plan.deferred:
        cycle_span.set_attribute("drain.outcome", "nothing-to-do")
        console.orch(f"Cycle {cycle_id} has no Todo/Backlog issues — nothing to do.")
        return 0

    console.startup_plan(
        cycle_id,
        [(i["identifier"], i["title"], model.resolve(i)) for i in plan.order],
    )

    for deferred in plan.deferred:
        issue = deferred["issue"]
        blocker_id = deferred["blocker_identifier"]
        blocker_state = deferred["blocker_state_type"]
        console.orch(
            f"deferred {issue['identifier']} — blocked by {blocker_id} ({blocker_state})"
        )

    if not plan.order:
        cycle_span.set_attribute("drain.outcome", "all-deferred")
        _emit_summary(log, total=0, halted_on=None)
        return 0

    in_tmux = bool(os.environ.get("TMUX"))
    current_pane_id: str | None = None

    # Per-repo base-branch baton for cross-issue stacking: maps a repo name
    # to the branch of the last issue that submitted PRs there, so the next
    # same-repo issue branches its worktree off that branch (a true Graphite
    # stack) instead of off ``main``. Mutated in place by each drained issue.
    repo_baton: dict[str, str] = {}

    total = len(plan.order)
    # Cycle queue rendered above the live stepper row, sourced from the
    # orchestrator's pick order — the renderer never recomputes this.
    # Mutated in place as each issue starts (queued → running) and finishes
    # (running → done), so a multi-issue drain shows live lane state.
    queue: list[swimlanes.QueueItem] = [
        swimlanes.QueueItem(identifier=i["identifier"], state="queued")
        for i in plan.order
    ]
    for index, issue in enumerate(plan.order):
        # Kill the pane from the previous issue before opening one for this issue.
        if current_pane_id is not None:
            watch_pane.close_pane(current_pane_id)
            current_pane_id = None

        _mark_queue(queue, issue["identifier"], "running")

        halt_code, current_pane_id = _drain_one_issue(
            issue,
            index=index,
            total=total,
            repos=repos,
            limits=limits,
            log=log,
            cycle_id=cycle_id,
            debug=debug,
            watch=watch,
            in_tmux=in_tmux,
            no_stack=no_stack,
            repo_baton=repo_baton,
            queue=queue,
        )
        if halt_code is not None:
            _emit_summary(log, total=total, halted_on=issue["identifier"])
            return halt_code  # type: ignore[return-value]

        _mark_queue(queue, issue["identifier"], "done")

        # Cycle-wide circuit breaker: every issue may stay under its own
        # per-issue caps while their sum drains the quota. Check the
        # running totals (which now include the Done issue just finished)
        # before spawning the next one; on breach, stop the cycle.
        cycle_breach = check_cycle(
            limits,
            tokens=log.cycle_tokens_cumulative(),
            cost_usd=log.cycle_cost_usd(),
            seconds=log.cycle_duration_seconds(),
        )
        if cycle_breach is not None:
            halt_reason = f"Halt: {cycle_breach.describe()}"
            log.set_cycle_halt(halt_reason)
            telemetry.mark_error(cycle_span, "err-cycle-breach", halt_reason)
            console.halt(halt_reason)
            _emit_summary(log, total=total, halted_on=halt_reason)
            return 1

    # Final pane is left open for scrollback (intentionally not killed here).
    cycle_span.set_attribute("drain.outcome", "drained")
    _emit_summary(log, total=total, halted_on=None)
    return 0


def _mark_queue(
    queue: list[swimlanes.QueueItem], identifier: str, state: str
) -> None:
    """In-place lane-state advance for the cycle queue. Unknown id is a no-op."""
    for i, item in enumerate(queue):
        if item.identifier == identifier:
            queue[i] = swimlanes.QueueItem(identifier=identifier, state=state)
            return


def _drain_one_issue(
    issue: dict,
    *,
    index: int,
    total: int,
    repos: Repos,
    limits: Limits,
    log: runlog.RunLog,
    cycle_id: str,
    debug: bool,
    watch: bool = False,
    in_tmux: bool = False,
    no_stack: bool = False,
    repo_baton: dict[str, str] | None = None,
    queue: list[swimlanes.QueueItem] | None = None,
) -> tuple[int | None, str | None]:
    """Drain a single issue end to end inside a ``drain.issue`` span.

    Returns ``(halt_code, pane_id)`` where ``halt_code`` is ``None`` when the
    issue reached Done (the caller then runs the cycle-wide circuit breaker and
    proceeds to the next issue), or an exit code when the run must halt.
    ``pane_id`` is the tmux pane ID opened for this issue (or ``None``).
    Each halt path is also marked on the span with a static ``exception.slug``.
    """
    identifier = issue["identifier"]
    with telemetry.tracer.start_as_current_span("drain.issue") as issue_span:
        issue_span.set_attribute("issue.identifier", identifier)
        issue_span.set_attribute("issue.title", issue["title"])
        issue_span.set_attribute("issue.index", index + 1)
        issue_span.set_attribute("issue.total", total)
        console.worker_event(identifier, f"picked: {issue['title']}")

        started_at = _now_iso()
        halt_ctx = _HaltContext(
            log=log,
            issue_span=issue_span,
            identifier=identifier,
            started_at=started_at,
        )
        try:
            target_repo = repos.resolve(issue)
        except RepoResolutionError as exc:
            # Pre-spawn resolution halt: no Linear state was moved, so no
            # revert is attempted. The worktree path is the ``<unresolved>``
            # placeholder since no repo was chosen.
            state_name = issue["state"]["name"]
            halt_reason = (
                f"{_halt_message(identifier, state_name, Path(_UNRESOLVED_WORKTREE_DISPLAY))}"
                f" — {exc}"
            )
            halt_ctx.record(
                slug="err-repo-resolution",
                halt_reason=halt_reason,
                final_linear_state=state_name,
                worktree_path=_UNRESOLVED_WORKTREE_DISPLAY,
                finished_at=_now_iso(),
                exit_code=-1,
            )
            return 1, None

        issue_span.set_attribute("issue.repo", target_repo.name)

        # Resume-attempt cap fires BEFORE any worktree manipulation or
        # Linear state change: a no-spawn refusal must leave the issue
        # exactly where it was so a future re-run (after the operator
        # raises the cap, clears prior runs, or finishes the work by
        # hand) can pick up cleanly. The cap is a *policy* knob, not a
        # runtime guardrail — no Breach is raised; see limits.py.
        #
        # ``max_resume_attempts=N`` allows up to N resumes after the
        # initial attempt (one fresh + N resumes = N+1 total halts
        # before refusal), matching the convention of ``max_retries`` in
        # the stdlib's urllib3/requests world. The cap fires once
        # ``prior_halts`` (count of non-Done entries for this issue in
        # the cycle's run logs, written by *prior* runs since a halt
        # exits this run) exceeds N.
        if limits.max_resume_attempts is not None:
            prior_halts = _resume_attempts(cycle_id, identifier)
            if prior_halts > limits.max_resume_attempts:
                planned_path = target_repo / worktree.WORKTREE_DIR / identifier
                state_name = issue["state"]["name"]
                halt_reason = (
                    f"{_halt_message(identifier, state_name, planned_path)}"
                    f" — resume-attempt cap reached "
                    f"(all {limits.max_resume_attempts} resumes used); "
                    "raise max_resume_attempts, clear prior runs, or finish by hand"
                )
                issue_span.set_attribute("issue.resumed", True)
                halt_ctx.record(
                    slug="err-resume-cap",
                    halt_reason=halt_reason,
                    final_linear_state=state_name,
                    worktree_path=str(planned_path),
                    finished_at=_now_iso(),
                    exit_code=-1,
                )
                return 1, None

        stack = target_repo.name not in repos.push_to_main_repos and not no_stack
        baton = repo_baton if repo_baton is not None else {}
        # Chain onto the prior same-repo issue's branch only in stack mode;
        # push-mode issues always branch off ``main`` (they land on main, so
        # there is no stack to extend).
        if stack:
            base = baton.get(target_repo.name, worktree.BASE_BRANCH)
        else:
            base = worktree.BASE_BRANCH
        try:
            handle = worktree.ensure(target_repo, identifier, base)
            worktree_path = handle.path
            # A reused worktree keeps the branch it was originally forked from;
            # the in-memory baton is empty across runs, so recover the true
            # base from the worktree itself rather than the recomputed value.
            if handle.resumed:
                base = worktree.read_base(worktree_path)
            issue_span.set_attribute("issue.base_branch", base)
            # A worktree checks out only tracked files, so gitignored
            # project config (.claude/, .mcp.json) is absent. Symlink it in
            # so the worker loads the same settings/hooks/agents/skills/MCP
            # as an interactive session at the repo root.
            worktree.link_project_config(
                target_repo, worktree_path, repos.worktree_config_paths
            )
            # Orchestrator owns the Todo→In Progress half so the lifecycle
            # doesn't depend on the spawned agent's compliance. The agent
            # still owns the …→Done half via Linear MCP — see prompt.py tail.
            linear.set_state(issue["id"], _IN_PROGRESS_STATE_NAME)
        except Exception as exc:
            # Convert any pre-spawn failure into a recorded halt rather than
            # a traceback: write a run-log entry with the planned worktree
            # path, print the halt message, exit non-zero. Subsequent issues
            # are not attempted — same contract as a spawn-time halt.
            planned_path = target_repo / worktree.WORKTREE_DIR / identifier
            state_name = issue["state"]["name"]
            halt_reason = (
                f"{_halt_message(identifier, state_name, planned_path)}"
                f" — setup failed: {exc}"
            )
            halt_ctx.record(
                slug="err-setup-failed",
                halt_reason=halt_reason,
                final_linear_state=state_name,
                worktree_path=str(planned_path),
                finished_at=_now_iso(),
                exit_code=-1,
            )
            return 1, None

        # Plant the stop-guard marker so the worker's Stop hook fires only
        # for drain sessions and knows which completion sequence to enforce.
        # Replaces any prior marker so a resumed worktree starts fresh.
        stop_guard.write_marker(worktree_path, mode="stack" if stack else "push")

        agent_prompt = prompt.build(
            issue, worktree_path, resumed=handle.resumed, base=base
        )
        issue_span.set_attribute("issue.resumed", handle.resumed)
        worker_model = model.resolve(issue)
        issue_span.set_attribute("issue.model", worker_model)

        debug_file = log.debug_path(identifier) if debug else None
        if debug_file is not None:
            console.worker_event(identifier, f"debug capture → {debug_file}")

        # Watch mode: run claude inside a tmux pane (so the operator sees the
        # live session) and read its stream-json off a FIFO instead of a
        # subprocess pipe. ``watch_pane.open_session`` returns ``None`` if the pane
        # or FIFO can't be brought up (having torn down its own partial state),
        # and we fall back to a normal subprocess spawn — the pane is a
        # convenience, not a requirement. ``pane_id`` is returned to the caller
        # for lifecycle management; ``external_stream``/``kill_fn`` steer the
        # worker onto the external path.
        session: watch_pane.WatchSession | None = None
        pane_id: str | None = None
        external_stream: TextIO | None = None
        kill_fn: Callable[[], None] | None = None
        if watch and in_tmux:
            argv = worker.build_argv(
                _CLAUDE_CMD,
                model=worker_model,
                prompt=agent_prompt,
                cost_limit_usd=limits.per_issue_cost_usd,
                debug_file=debug_file,
            )
            session = watch_pane.open_session(argv, worktree_path)
            if session is not None:
                pane_id = session.pane_id
                external_stream = session.stream
                kill_fn = session.kill

        marker: dict = {
            "pid": os.getpid(),
            "cycle_id": cycle_id,
            "run_log_path": str(log.path),
            "issue": {
                "identifier": identifier,
                "title": issue["title"],
                "repo": target_repo.name,
                "worktree_path": str(worktree_path),
            },
            "model": worker_model,
            "started_at": started_at,
            "index": index + 1,
            "total": total,
            "progress": {},
        }
        progress.write(marker)

        def _make_on_progress(m: dict, ident: str):
            def _cb(
                turns: int,
                cumulative_tokens: int,
                peak_context_tokens: int,
                cost_usd: float | None,
                elapsed_seconds: float,
            ) -> None:
                m["progress"] = {
                    "turns": turns,
                    "cumulative_tokens": cumulative_tokens,
                    "peak_context_tokens": peak_context_tokens,
                    "cost_usd": cost_usd,
                    "elapsed_seconds": elapsed_seconds,
                    "last_event_at": _now_iso(),
                }
                progress.write(m)
                console.worker_event(
                    ident,
                    f"turn {turns} · {progress.fmt_tokens(cumulative_tokens)} tok"
                    f" (peak {progress.fmt_tokens(peak_context_tokens)})"
                    f" · {progress.fmt_elapsed(elapsed_seconds)}",
                )
            return _cb

        outcome_verdict: dict | None = None
        prep_verdict: dict | None = None
        responder_runs: list[dict] = []

        step_renderer = swimlanes.build_renderer(
            sys.stderr, worktree_path=worktree_path, queue=queue
        )
        keyboard = swimlanes.KeyboardListener(step_renderer)
        keyboard.start()

        # Chain the swimlanes proof-of-life sub-status onto the existing
        # progress callback so the renderer flips the active step's sub-line
        # on every new assistant turn (`turn N · X tok · 12.3s`).
        base_on_progress = _make_on_progress(marker, identifier)

        def _on_progress(
            turns: int,
            cumulative_tokens: int,
            peak_context_tokens: int,
            cost_usd: float | None,
            elapsed_seconds: float,
        ) -> None:
            base_on_progress(
                turns, cumulative_tokens, peak_context_tokens, cost_usd, elapsed_seconds
            )
            step_renderer.on_progress(turns, cumulative_tokens, elapsed_seconds)

        try:
            result = worker.run_issue(
                claude_cmd=_CLAUDE_CMD,
                model=worker_model,
                prompt=agent_prompt,
                cwd=worktree_path,
                token_limit=limits.per_issue_tokens,
                time_limit_seconds=limits.per_issue_seconds,
                cost_limit_usd=limits.per_issue_cost_usd,
                debug_file=debug_file,
                external_stream=external_stream,
                kill_fn=kill_fn,
                on_progress=_on_progress,
                on_step=step_renderer.feed,
                passthrough=console.AgentSink(),
            )
        finally:
            keyboard.stop()
            step_renderer.finalize()
            progress.clear()
            if session is not None:
                session.cleanup()
        finished_at = _now_iso()
        # Read any verdicts the worker recorded in the handoff. M2+-populated
        # locals take precedence; handoff fills the gap when they are None.
        _hov, _hpv, _hrv = handoff.read_partial(worktree_path)
        outcome = _WorkerOutcome(
            result=result,
            outcome_verdict=outcome_verdict if outcome_verdict is not None else _hov,
            prep_verdict=prep_verdict if prep_verdict is not None else _hpv,
            review_verdict=_hrv,
            responder_runs=responder_runs,
        )

        if result.breach is not None:
            # The worker crossed a per-issue cap (tokens or time) and was
            # process-group killed (grandchildren reaped). Same revert + halt
            # contract as a not-Done exit, with the recorded usage of the
            # killed session and the breached cap named in the halt reason.
            original_state_name = issue["state"]["name"]
            effective_state, revert_error = _revert_to_pre_halt_state(
                issue["id"],
                target_state_name=original_state_name,
                pre_revert_state_name=_IN_PROGRESS_STATE_NAME,
            )
            halt_reason = (
                f"{_halt_message(identifier, effective_state, worktree_path)}"
                f" — {result.breach.describe()}"
            )
            if revert_error is not None:
                halt_reason += (
                    f"; revert to {original_state_name!r} failed: {revert_error}"
                )
            issue_span.set_attribute("issue.exit_code", result.exit_code)
            _set_verdict_span_attrs(issue_span, outcome)
            halt_ctx.record(
                slug="err-per-issue-breach",
                halt_reason=halt_reason,
                final_linear_state=effective_state,
                worktree_path=str(worktree_path),
                finished_at=finished_at,
                exit_code=result.exit_code,
                outcome=outcome,
            )
            return 1, pane_id

        refreshed = linear.get_issue(issue["id"])
        post_spawn_state = refreshed["state"]["name"]
        is_done = refreshed["state"]["type"] == _DONE_STATE_TYPE
        issue_span.set_attribute("issue.exit_code", result.exit_code)
        issue_span.set_attribute("issue.is_done", is_done)

        finishing_runs: list[dict] = []
        finishing_attempted = False

        # The submission signal is ``pr_urls`` in exec-state.json, not the Linear
        # Done state. A stack worker that submitted its PR(s) and deliberately
        # left the issue In Progress (governance: stay In Progress until the PR
        # merges) is complete — read that signal up front so both the recovery
        # condition and the success gate below key on submission rather than on
        # Done. Push mode has no handoff, so this is always ``None`` there.
        submitted = handoff.read(worktree_path) if stack else None

        # Recovery: committed-but-unsubmitted → spawn a sonnet finishing sub-agent
        # before halting. Fires only when the work was not already submitted (a
        # present ``pr_urls`` means the worker finished and left the issue In
        # Progress — no recovery needed), the branch has commits beyond base (an
        # empty or uncommitted-only branch is a genuine failure), the verifier did
        # not explicitly reject the work (a FAIL verdict must stay halted to
        # satisfy KR2), and a cap breach did not already stop the session (that
        # path returned above). At most one finishing attempt per issue per run.
        if not is_done and submitted is None and _commits_beyond_base(worktree_path, base):
            _prior_verifier_failed = (
                outcome.outcome_verdict is not None
                and outcome.outcome_verdict.get("result") == "fail"
            )
            if not _prior_verifier_failed:
                finishing_attempted = True
                console.worker_event(
                    identifier, "not-Done with commits: spawning finishing sub-agent"
                )
                _finishing_started = _now_iso()
                # The finishing agent runs on the spawned (non-pane) path, so the
                # watch split-pane can't mirror it. Route its per-turn progress
                # through ``on_progress`` so the orchestrator pane keeps ticking —
                # otherwise a normal multi-minute finishing run reads as a hang.
                _finishing_result = worker.run_issue(
                    claude_cmd=_CLAUDE_CMD,
                    model=_FINISHING_MODEL,
                    prompt=prompt.build_finishing(
                        identifier, worktree_path, base, stack=stack
                    ),
                    cwd=worktree_path,
                    token_limit=limits.per_issue_tokens,
                    time_limit_seconds=limits.per_issue_seconds,
                    cost_limit_usd=limits.per_issue_cost_usd,
                    on_progress=_make_on_progress(marker, identifier),
                    passthrough=console.AgentSink(),
                )
                _finishing_finished = _now_iso()
                _log_finishing_done(identifier, _finishing_result)
                finishing_runs.append({
                    "trigger": "err-issue-not-done",
                    "started_at": _finishing_started,
                    "finished_at": _finishing_finished,
                    **_worker_log_fields(_finishing_result),
                })
                finished_at = _finishing_finished
                # Re-read to see whether finishing succeeded. In stack mode the
                # finishing agent records ``pr_urls`` and (per governance) leaves
                # the issue In Progress, so re-read the submission signal too —
                # the success gate below accepts it whether or not Done was set.
                refreshed = linear.get_issue(issue["id"])
                post_spawn_state = refreshed["state"]["name"]
                is_done = refreshed["state"]["type"] == _DONE_STATE_TYPE
                issue_span.set_attribute("issue.is_done", is_done)
                submitted = handoff.read(worktree_path) if stack else None
                # Pull any verdicts the finishing agent wrote to the handoff so
                # the verifier gate below uses the freshest available signal.
                _fhov, _fhpv, _fhrv = handoff.read_partial(worktree_path)
                if _fhov is not None or _fhpv is not None or _fhrv is not None:
                    outcome = dataclass_replace(
                        outcome,
                        outcome_verdict=_fhov if _fhov is not None else outcome.outcome_verdict,
                        prep_verdict=_fhpv if _fhpv is not None else outcome.prep_verdict,
                        review_verdict=_fhrv if _fhrv is not None else outcome.review_verdict,
                    )

        if is_done or submitted is not None:
            # Success gate. The cycle completes when the worker either marked the
            # issue Done (push mode: the push to main is the completion proof) or
            # left a non-empty ``pr_urls`` (stack mode: the submitted PR is the
            # proof, and the issue stays In Progress until that PR merges).
            #
            # Stack-mode confirmation, read before teardown removes the worktree:
            # a stack issue that reached here on Done alone but left no ``pr_urls``
            # was marked Done without opening a PR. Revert + halt, preserve the
            # worktree for inspection, and do NOT extend the baton — the next
            # same-repo issue must not stack onto a branch that was never pushed.
            # Push-mode issues have no stack to extend and no handoff, so they
            # bypass this confirmation entirely.
            if stack and submitted is None:
                # Recovery: Done but no pr_urls → spawn finishing sub-agent once
                # (guard skips a second attempt if not-Done recovery already ran).
                if not finishing_attempted and _commits_beyond_base(worktree_path, base):
                    finishing_attempted = True
                    console.worker_event(
                        identifier, "Done but no pr_urls: spawning finishing sub-agent"
                    )
                    _finishing_started = _now_iso()
                    _finishing_result = worker.run_issue(
                        claude_cmd=_CLAUDE_CMD,
                        model=_FINISHING_MODEL,
                        prompt=prompt.build_finishing(
                            identifier, worktree_path, base, stack=stack
                        ),
                        cwd=worktree_path,
                        token_limit=limits.per_issue_tokens,
                        time_limit_seconds=limits.per_issue_seconds,
                        cost_limit_usd=limits.per_issue_cost_usd,
                        on_progress=_make_on_progress(marker, identifier),
                        passthrough=console.AgentSink(),
                    )
                    _finishing_finished = _now_iso()
                    _log_finishing_done(identifier, _finishing_result)
                    finishing_runs.append({
                        "trigger": "err-stack-no-prs",
                        "started_at": _finishing_started,
                        "finished_at": _finishing_finished,
                        **_worker_log_fields(_finishing_result),
                    })
                    finished_at = _finishing_finished
                    submitted = handoff.read(worktree_path)
                    # Refresh state name: the finishing agent may have moved the
                    # issue away from Done (e.g. back to In Progress on partial
                    # failure). Without this, _revert_to_pre_halt_state reports
                    # the wrong pre-revert state on revert failure.
                    _refreshed_post = linear.get_issue(issue["id"])
                    post_spawn_state = _refreshed_post["state"]["name"]
                    # Propagate any verdicts the finishing agent wrote so the
                    # verifier gate below reads the freshest signal.
                    if submitted is not None:
                        _fhov, _fhpv, _fhrv = handoff.read_partial(worktree_path)
                        if _fhov is not None or _fhpv is not None or _fhrv is not None:
                            outcome = dataclass_replace(
                                outcome,
                                outcome_verdict=_fhov if _fhov is not None else outcome.outcome_verdict,
                                prep_verdict=_fhpv if _fhpv is not None else outcome.prep_verdict,
                                review_verdict=_fhrv if _fhrv is not None else outcome.review_verdict,
                            )

                if stack and submitted is None:
                    original_state_name = issue["state"]["name"]
                    effective_state, revert_error = _revert_to_pre_halt_state(
                        issue["id"],
                        target_state_name=original_state_name,
                        pre_revert_state_name=post_spawn_state,
                    )
                    halt_reason = (
                        f"{_halt_message(identifier, effective_state, worktree_path)}"
                        " — marked Done but exec-state.json has no submitted "
                        "pr_urls; stack chain halted"
                    )
                    if finishing_runs:
                        halt_reason += (
                            " — finishing sub-agent attempted but did not produce pr_urls"
                        )
                    if revert_error is not None:
                        halt_reason += (
                            f" — revert to {original_state_name!r} failed: {revert_error}"
                        )
                    halt_ctx.record(
                        slug="err-stack-no-prs",
                        halt_reason=halt_reason,
                        final_linear_state=effective_state,
                        worktree_path=str(worktree_path),
                        finished_at=finished_at,
                        exit_code=result.exit_code,
                        outcome=outcome,
                        finishing_runs=finishing_runs,
                    )
                    return 1, pane_id

            # Outcome-verifier gate: a fail verdict stops the cycle and leaves
            # the worktree intact for operator inspection — default-to-halt,
            # not default-to-continue. Any other result value (including "pass")
            # is treated as a pass and lets the drain continue.
            verifier_fail = (
                outcome.outcome_verdict is not None
                and outcome.outcome_verdict.get("result") == "fail"
            )
            if verifier_fail:
                original_state_name = issue["state"]["name"]
                effective_state, revert_error = _revert_to_pre_halt_state(
                    issue["id"],
                    target_state_name=original_state_name,
                    pre_revert_state_name=post_spawn_state,
                )
                findings = outcome.outcome_verdict.get("findings") or []
                detail = f"outcome verifier fail ({len(findings)} finding(s))"
                halt_reason = (
                    f"{_halt_message(identifier, effective_state, worktree_path)}"
                    f" — {detail}"
                )
                if revert_error is not None:
                    halt_reason += (
                        f"; revert to {original_state_name!r} failed: {revert_error}"
                    )
                _set_verdict_span_attrs(issue_span, outcome)
                halt_ctx.record(
                    slug="err-outcome-verifier-fail",
                    halt_reason=halt_reason,
                    final_linear_state=effective_state,
                    worktree_path=str(worktree_path),
                    finished_at=finished_at,
                    exit_code=result.exit_code,
                    outcome=outcome,
                    finishing_runs=finishing_runs,
                )
                # set_cycle_halt is called here (not via the _run() cycle-cap
                # path) because the spec requires cycle_halt_reason to name the
                # verifier findings so downstream tooling can distinguish this
                # halt type from a cap breach or a worker not-Done.
                halt_ctx.log.set_cycle_halt(halt_reason)
                return 1, pane_id

            if submitted is not None:
                # Hand the baton to the next same-repo issue and record the
                # submitted PRs as the orchestrator's confirmation line.
                baton[target_repo.name] = identifier
                issue_span.set_attribute("issue.submitted_pr_count", len(submitted.pr_urls))
                pr_list = ", ".join(pr.url for pr in submitted.pr_urls)
                console.worker_event(identifier, f"submitted {pr_list}")

            issue_span.set_attribute("issue.final_linear_state", post_spawn_state)

            remove_error: str | None = None
            try:
                worktree.merge_entire_sessions(worktree_path, target_repo)
                worktree.remove(target_repo, worktree_path)
            except RuntimeError as exc:
                remove_error = str(exc)
                issue_span.set_attribute("worktree.remove_error", remove_error)
                console.orch(f"{identifier}: worktree teardown failed: {exc}")
            # Append unconditionally for every attempted issue.
            log.append_entry(
                issue_identifier=identifier,
                started_at=started_at,
                finished_at=finished_at,
                exit_code=result.exit_code,
                final_linear_state=post_spawn_state,
                worktree_path=str(worktree_path),
                halt_reason=remove_error,
                outcome_verdict=outcome.outcome_verdict,
                prep_verdict=outcome.prep_verdict,
                review_verdict=outcome.review_verdict,
                responder_runs=outcome.responder_runs,
                finishing_runs=finishing_runs,
                **_worker_log_fields(outcome.result),
            )
            _set_verdict_span_attrs(issue_span, outcome)
            if remove_error is None:
                console.worker_event(identifier, "done; worktree removed")
            return None, pane_id

        # Not-Done halt: revert to the pre-halt state so a re-run picks
        # this issue back up instead of silently skipping it.
        original_state_name = issue["state"]["name"]
        effective_state, revert_error = _revert_to_pre_halt_state(
            issue["id"],
            target_state_name=original_state_name,
            pre_revert_state_name=post_spawn_state,
        )
        halt_reason = _halt_message(identifier, effective_state, worktree_path)
        tripped = stop_guard.read_tripped(worktree_path)
        if tripped is not None:
            halt_reason += f" — {stop_guard.TRIPPED_HALT_REASON}: {tripped}"
        if finishing_runs:
            halt_reason += " — finishing sub-agent attempted but did not mark Done"
        if revert_error is not None:
            halt_reason += (
                f" — revert to {original_state_name!r} failed: {revert_error}"
            )
        # halt_reason carries the same string also printed to stderr below
        # so the on-disk and terminal surfaces cannot drift.
        _set_verdict_span_attrs(issue_span, outcome)
        halt_ctx.record(
            slug="err-issue-not-done",
            halt_reason=halt_reason,
            final_linear_state=effective_state,
            worktree_path=str(worktree_path),
            finished_at=finished_at,
            exit_code=result.exit_code,
            outcome=outcome,
            finishing_runs=finishing_runs,
        )
        return 1, pane_id
