"""Drain a cycle by iterating over its sorted Todo/Backlog issues.

Halt-on-not-Done, the orchestrator-owned Todo→In-Progress transition, the
run-log artefact, and the inspectable-halt UX all live here. The halt-message
helper ``_halt_message`` is the single source of truth for the operator-facing
halt string — emitted both on stderr and into the run-log entry's
``halt_reason`` field.
"""
from __future__ import annotations

import fcntl
import json
import os
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO

from opentelemetry.trace import Span

from . import console, flow, grade_draft, graphite, handoff, linear, model, progress, prompt, runlog, telemetry, worker, worktree
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
See ``docs/design-decisions.md`` §10."""
_UNRESOLVED_WORKTREE_DISPLAY = "<unresolved>"
"""Worktree-path placeholder for the pre-spawn resolution-halt path.
No path has been chosen yet — the issue couldn't be mapped to a target
repo — so the run-log entry and stderr halt line carry this marker
rather than a misleading fake path."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


_WATCH_FIFO_TIMEOUT_SECONDS = 10.0
"""How long ``_open_fifo_stream`` waits for the pane's ``claude | tee`` to
produce its first bytes before giving up and falling back to a normal
subprocess spawn. The pane's ``claude`` emits its init event within seconds;
this bound only guards a pane that never started (tmux accepted the
split-window but the command died), so the drain never wedges on a dead FIFO."""


def _watch_formatter_stage() -> str:
    """The pane-visible pipe stage that renders stream-json human-readable.

    ``<python> -u -m drain_cycle.watch_format`` reads the operator's copy and
    writes formatted activity; ``-u`` keeps it unbuffered so the pane updates
    live. ``sys.executable`` is the interpreter drain-cycle itself runs under,
    so for the editable uv-tool install it is the venv that has ``drain_cycle``
    importable.

    The whole thing is a brace group with ``|| cat`` so the FIFO branch stays
    truly independent of the formatter. A bare ``tee <fifo> | formatter`` is
    *not* independent: if the formatter can't launch or dies, ``tee`` takes
    SIGPIPE on its stdout and exits, closing the FIFO write end and truncating
    drain-cycle's parse. The brace-group subshell holds the pipe's read end
    open across the formatter→``cat`` transition, so ``tee`` never sees a
    readerless pipe — worst case the pane falls back to raw JSON via ``cat``.
    """
    py = shlex.quote(sys.executable)
    return f"{{ {py} -u -m drain_cycle.watch_format || cat; }}"


def _open_watch_pane(argv: list[str], cwd: Path) -> tuple[str, Path] | None:
    """Open a tmux split-pane running ``argv`` piped through ``tee`` into a FIFO.

    The pane *is* the ``claude`` session: ``argv | tee <fifo> | <formatter>``
    splits the stream — the FIFO branch carries byte-for-byte stream-json to
    drain-cycle's reader, while the pane copy flows through the formatter (see
    ``_watch_formatter_stage``) so the operator sees human-readable activity
    rather than raw JSON. ``exec ${SHELL}`` after the pipeline keeps the pane
    alive for scrollback once ``claude`` exits (``tee`` still closes the FIFO
    write end, so the reader reaches EOF).

    ``split-window -P -F "#{pane_id}"`` prints the new pane's ID directly so we
    capture the session pane, not the operator's active pane; ``-c`` runs it in
    the issue's worktree. Returns ``(pane_id, fifo_path)``, or ``None`` if the
    FIFO or pane could not be created — every failure (tmux not on PATH,
    non-zero exit, any OS error) is swallowed so a broken tmux environment
    falls back to a normal spawn rather than crashing the drain.
    """
    try:
        fifo_path = Path(tempfile.mkdtemp(prefix="drain-watch-")) / "stream.fifo"
        os.mkfifo(fifo_path)
    except OSError:
        return None
    pipeline = (
        " ".join(shlex.quote(a) for a in argv)
        + f" | tee {shlex.quote(str(fifo_path))}"
        + f" | {_watch_formatter_stage()}"
        + "; exec ${SHELL:-/bin/sh}"
    )
    try:
        result = subprocess.run(
            [
                "tmux", "split-window", "-d",
                "-P", "-F", "#{pane_id}",
                "-c", str(cwd),
                pipeline,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        pane_id = result.stdout.strip()
    except Exception:
        _cleanup_fifo(fifo_path)
        return None
    if not pane_id:
        _cleanup_fifo(fifo_path)
        return None
    return pane_id, fifo_path


def _open_fifo_stream(fifo_path: Path, timeout: float) -> TextIO | None:
    """Open ``fifo_path`` for reading, waiting up to ``timeout`` for a writer.

    The FIFO is opened non-blocking so a pane that never started can't wedge
    the drain; ``select`` then waits for the first bytes (``tee`` writing the
    pane's first stream-json line). Once readable, ``O_NONBLOCK`` is cleared so
    the reader thread's line iteration blocks normally until EOF. Returns a
    line-buffered text stream, or ``None`` if no writer/data appeared in time
    (the caller then tears down the pane and falls back to a normal spawn).
    """
    try:
        fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    readable, _, _ = select.select([fd], [], [], timeout)
    if not readable:
        os.close(fd)
        return None
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
    try:
        return os.fdopen(fd, "r", buffering=1)
    except OSError:
        os.close(fd)
        return None


def _cleanup_fifo(fifo_path: Path) -> None:
    """Remove the FIFO and its temp dir; swallows all errors."""
    shutil.rmtree(fifo_path.parent, ignore_errors=True)


def _close_watch_pane(pane_id: str) -> None:
    """Kill a tmux pane by ID; swallows all errors."""
    try:
        subprocess.run(
            ["tmux", "kill-pane", "-t", pane_id],
            check=False,
            capture_output=True,
        )
    except Exception:
        pass


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
) -> int:
    """Drain the current cycle inside the ``drain.cycle`` root span.

    The span wrapper is thin so the body keeps its shape; per-issue work nests
    under it via ``_drain_one_issue``'s ``drain.issue`` spans, and the Linear,
    worktree, and worker spans nest under those — yielding one trace per drain.
    """
    if limits is None:
        limits = Limits()
    with telemetry.tracer.start_as_current_span("drain.cycle") as cycle_span:
        return _run(repos, limits, cycle_span, watch=watch, no_stack=no_stack)


def _run(
    repos: Repos,
    limits: Limits,
    cycle_span: Span,
    *,
    watch: bool = False,
    no_stack: bool = False,
) -> int:
    debug = _debug_enabled()
    cycle_id = linear.current_cycle_id()
    cycle_span.set_attribute("drain.cycle_id", cycle_id)
    log = runlog.RunLog(cycle_id=cycle_id)
    try:
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
    last_branch_per_repo: dict[str, str] = {}

    total = len(plan.order)
    for index, issue in enumerate(plan.order):
        # Kill the pane from the previous issue before opening one for this issue.
        if current_pane_id is not None:
            _close_watch_pane(current_pane_id)
            current_pane_id = None

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
            last_branch_per_repo=last_branch_per_repo,
        )
        if halt_code is not None:
            _emit_summary(log, total=total, halted_on=issue["identifier"])
            return halt_code  # type: ignore[return-value]

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
    last_branch_per_repo: dict[str, str] | None = None,
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

        flow_name = flow.resolve(issue)
        if flow_name is not None:
            issue_span.set_attribute("issue.flow", flow_name)
            issue_span.set_attribute("issue.responder_run_count", 0)

        started_at = _now_iso()
        is_verify = prompt.is_verify_flow(issue)
        issue_span.set_attribute("issue.verify_flow", is_verify)
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
            log.append_entry(
                issue_identifier=identifier,
                started_at=started_at,
                finished_at=_now_iso(),
                exit_code=-1,
                final_linear_state=state_name,
                worktree_path=_UNRESOLVED_WORKTREE_DISPLAY,
                halt_reason=halt_reason,
                flow=flow_name,
                shape_task_invoked=is_verify,
            )
            issue_span.set_attribute("issue.final_linear_state", state_name)
            telemetry.mark_error(issue_span, "err-repo-resolution", halt_reason)
            console.halt(halt_reason)
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
                log.append_entry(
                    issue_identifier=identifier,
                    started_at=started_at,
                    finished_at=_now_iso(),
                    exit_code=-1,
                    final_linear_state=state_name,
                    worktree_path=str(planned_path),
                    halt_reason=halt_reason,
                    flow=flow_name,
                    shape_task_invoked=is_verify,
                )
                issue_span.set_attribute("issue.final_linear_state", state_name)
                issue_span.set_attribute("issue.resumed", True)
                telemetry.mark_error(issue_span, "err-resume-cap", halt_reason)
                console.halt(halt_reason)
                return 1, None

        stack = target_repo.name not in repos.push_to_main_repos and not no_stack
        base = (
            last_branch_per_repo.get(target_repo.name, worktree.BASE_BRANCH)
            if stack and last_branch_per_repo is not None
            else worktree.BASE_BRANCH
        )
        try:
            handle = worktree.ensure(target_repo, identifier, base)
            worktree_path = handle.path
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
            log.append_entry(
                issue_identifier=identifier,
                started_at=started_at,
                finished_at=_now_iso(),
                exit_code=-1,
                final_linear_state=state_name,
                worktree_path=str(planned_path),
                halt_reason=halt_reason,
                flow=flow_name,
                shape_task_invoked=is_verify,
            )
            issue_span.set_attribute("issue.final_linear_state", state_name)
            telemetry.mark_error(issue_span, "err-setup-failed", halt_reason)
            console.halt(halt_reason)
            return 1, None

        agent_prompt = prompt.build(issue, worktree_path, resumed=handle.resumed, stack=stack)
        issue_span.set_attribute("issue.resumed", handle.resumed)
        worker_model = model.resolve(issue)
        issue_span.set_attribute("issue.model", worker_model)

        debug_file = log.debug_path(identifier) if debug else None
        if debug_file is not None:
            console.worker_event(identifier, f"debug capture → {debug_file}")

        # Watch mode: run claude inside a tmux pane (so the operator sees the
        # live session) and read its stream-json off a FIFO instead of a
        # subprocess pipe. If the pane or FIFO can't be brought up, fall back
        # to a normal subprocess spawn — the pane is a convenience, not a
        # requirement. ``pane_id`` is returned to the caller for lifecycle
        # management; ``external_stream``/``kill_fn`` steer the worker onto the
        # external path.
        pane_id: str | None = None
        fifo_path: Path | None = None
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
            opened = _open_watch_pane(argv, worktree_path)
            if opened is not None:
                pane_id, fifo_path = opened
                external_stream = _open_fifo_stream(
                    fifo_path, _WATCH_FIFO_TIMEOUT_SECONDS
                )
                if external_stream is None:
                    # Pane accepted but produced no output in time — tear it
                    # down so we don't double-spawn, then fall back.
                    _close_watch_pane(pane_id)
                    _cleanup_fifo(fifo_path)
                    pane_id = None
                    fifo_path = None
                else:
                    def kill_fn() -> None:
                        _close_watch_pane(pane_id)

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

        # Verify-flow verdict and responder data — populated by M2+ roles.
        outcome_verdict: dict | None = None
        prep_verdict: dict | None = None
        responder_runs: list[dict] = []

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
                on_progress=_make_on_progress(marker, identifier),
                passthrough=console.AgentSink(),
            )
        finally:
            progress.clear()
            if fifo_path is not None:
                _cleanup_fifo(fifo_path)
        finished_at = _now_iso()

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
            log.append_entry(
                issue_identifier=identifier,
                started_at=started_at,
                finished_at=finished_at,
                exit_code=result.exit_code,
                final_linear_state=effective_state,
                worktree_path=str(worktree_path),
                halt_reason=halt_reason,
                flow=flow_name,
                outcome_verdict=outcome_verdict,
                prep_verdict=prep_verdict,
                responder_runs=responder_runs,
                shape_task_invoked=is_verify,
                **_worker_log_fields(result),
            )
            issue_span.set_attribute("issue.exit_code", result.exit_code)
            issue_span.set_attribute("issue.final_linear_state", effective_state)
            if outcome_verdict is not None:
                issue_span.set_attribute("issue.outcome_verdict", outcome_verdict["result"])
            if prep_verdict is not None:
                issue_span.set_attribute("issue.prep_verdict", prep_verdict["result"])
            if flow_name is not None:
                issue_span.set_attribute("issue.responder_run_count", len(responder_runs))
            telemetry.mark_error(issue_span, "err-per-issue-breach", halt_reason)
            console.halt(halt_reason)
            return 1, pane_id

        refreshed = linear.get_issue(issue["id"])
        post_spawn_state = refreshed["state"]["name"]
        is_done = refreshed["state"]["type"] == _DONE_STATE_TYPE
        issue_span.set_attribute("issue.exit_code", result.exit_code)
        issue_span.set_attribute("issue.is_done", is_done)

        if is_done:
            issue_span.set_attribute("issue.final_linear_state", post_spawn_state)
            submitted_pr: graphite.PrInfo | None = None
            review_high: bool | None = None
            parent_branch: str | None = None
            if stack and last_branch_per_repo is not None:
                # Read the agent's handoff and determine PR review priority.
                hd = handoff.read(worktree_path)
                findings = hd.findings if hd is not None else {}
                review_high = (
                    "review:high" in issue["labels"]
                    or bool(findings.get("critical"))
                    or bool(findings.get("required"))
                )

                # Assemble into the per-repo Graphite stack. Any gt/gh failure is
                # stop-the-line: the next issue would otherwise chain onto an
                # uncertain push state. Leave the worktree on disk for recovery.
                parent_branch = (
                    last_branch_per_repo.get(target_repo.name) or worktree.BASE_BRANCH
                )
                graphite_error: str | None = None
                try:
                    submitted_pr = graphite.submit(
                        worktree_path,
                        parent=parent_branch,
                        title=hd.pr_title if hd is not None else "",
                        body=hd.pr_body if hd is not None else "",
                    )
                    if review_high:
                        graphite.ensure_review_high_label(worktree_path)
                        graphite.add_label(
                            submitted_pr.number, "review:high", worktree_path
                        )
                    last_branch_per_repo[target_repo.name] = identifier
                except RuntimeError as exc:
                    graphite_error = str(exc)

                if graphite_error is not None:
                    # The agent already marked the issue Done, but no PR exists.
                    # Revert out of Done so a re-run picks the issue back up and
                    # re-attempts the stack submission against the worktree (left
                    # on disk below). Mirrors the not-Done halt path.
                    original_state_name = issue["state"]["name"]
                    effective_state, revert_error = _revert_to_pre_halt_state(
                        issue["id"],
                        target_state_name=original_state_name,
                        pre_revert_state_name=post_spawn_state,
                    )
                    try:
                        linear.add_comment(
                            issue["id"],
                            f"Reverted from Done: stacked PR submission failed — "
                            f"{graphite_error}. Worktree preserved for re-run.",
                        )
                    except Exception as exc:
                        print(
                            f"drain-cycle: {identifier}: Linear blocker comment failed: {exc}",
                            file=sys.stderr,
                        )
                    halt_reason = (
                        f"{_halt_message(identifier, effective_state, worktree_path)}"
                        f" — graphite: {graphite_error}"
                    )
                    if revert_error is not None:
                        halt_reason += (
                            f" — revert to {original_state_name!r} failed: {revert_error}"
                        )
                    log.append_entry(
                        issue_identifier=identifier,
                        started_at=started_at,
                        finished_at=finished_at,
                        exit_code=result.exit_code,
                        final_linear_state=effective_state,
                        worktree_path=str(worktree_path),
                        halt_reason=halt_reason,
                        flow=flow_name,
                        outcome_verdict=outcome_verdict,
                        prep_verdict=prep_verdict,
                        responder_runs=responder_runs,
                        shape_task_invoked=is_verify,
                        **_worker_log_fields(result),
                    )
                    telemetry.mark_error(issue_span, "err-graphite", halt_reason)
                    console.halt(halt_reason)
                    return 1, pane_id

            # Post the PR link back to the Linear issue. Informational only:
            # a comment failure never halts the drain.
            if submitted_pr is not None:
                comment_lines = [f"PR: {submitted_pr.url}"]
                if review_high:
                    comment_lines.append("review:high — flagged for operator review")
                try:
                    linear.add_comment(issue["id"], "\n".join(comment_lines))
                except Exception as exc:
                    console.orch(
                        f"{identifier}: Linear PR comment failed: {exc}"
                    )

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
                flow=flow_name,
                outcome_verdict=outcome_verdict,
                prep_verdict=prep_verdict,
                responder_runs=responder_runs,
                shape_task_invoked=is_verify,
                pr_url=submitted_pr.url if submitted_pr is not None else None,
                pr_number=submitted_pr.number if submitted_pr is not None else None,
                review_high=review_high,
                parent_branch=parent_branch if submitted_pr is not None else None,
                **_worker_log_fields(result),
            )
            if outcome_verdict is not None:
                issue_span.set_attribute("issue.outcome_verdict", outcome_verdict["result"])
            if prep_verdict is not None:
                issue_span.set_attribute("issue.prep_verdict", prep_verdict["result"])
            if flow_name is not None:
                issue_span.set_attribute("issue.responder_run_count", len(responder_runs))
            try:
                draft_path = grade_draft.write_draft_from_entry(
                    identifier, log.entries[-1]
                )
                console.worker_event(identifier, f"grade draft → {draft_path}")
            except Exception as exc:
                console.orch(
                    f"{identifier}: grade-draft write failed (non-fatal): {exc}"
                )
            if submitted_pr is not None:
                console.worker_event(identifier, f"done; PR {submitted_pr.url}")
            elif remove_error is None:
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
        if revert_error is not None:
            halt_reason += (
                f" — revert to {original_state_name!r} failed: {revert_error}"
            )
        # halt_reason carries the same string also printed to stderr below
        # so the on-disk and terminal surfaces cannot drift.
        log.append_entry(
            issue_identifier=identifier,
            started_at=started_at,
            finished_at=finished_at,
            exit_code=result.exit_code,
            final_linear_state=effective_state,
            worktree_path=str(worktree_path),
            halt_reason=halt_reason,
            flow=flow_name,
            outcome_verdict=outcome_verdict,
            prep_verdict=prep_verdict,
            responder_runs=responder_runs,
            shape_task_invoked=is_verify,
            **_worker_log_fields(result),
        )
        issue_span.set_attribute("issue.final_linear_state", effective_state)
        if outcome_verdict is not None:
            issue_span.set_attribute("issue.outcome_verdict", outcome_verdict["result"])
        if prep_verdict is not None:
            issue_span.set_attribute("issue.prep_verdict", prep_verdict["result"])
        if flow_name is not None:
            issue_span.set_attribute("issue.responder_run_count", len(responder_runs))
        telemetry.mark_error(issue_span, "err-issue-not-done", halt_reason)
        console.halt(halt_reason)
        return 1, pane_id
