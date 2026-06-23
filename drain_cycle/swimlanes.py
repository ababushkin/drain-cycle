"""Stream-derived swimlanes view.

Reads the same stream-json events drain-cycle already drains and surfaces the
active `exec:*` step on stderr. The parser is the single source of truth for
"is this content block a Skill delegation, and which skill" — extracted from
``watch_format`` so the pane filter and the live-output renderer agree by
construction rather than by parallel implementations drifting apart.

Redraw mechanism: a DEC scroll region (DECSTBM, ``\\x1b[<top>;<bottom>r``) pins
the bottom N rows as an owned status block. The worker passthrough scrolls
within the region above; the renderer paints the pinned rows with absolute
cursor positioning (``\\x1b[<row>;<col>H``) bracketed by save/restore cursor
(``\\x1b7``/``\\x1b8``) so the cursor returns to the scrolling area between
repaints. Rejected ``rich.Live`` (owns the cursor on a refresh loop, would
suppress the append-only writes the operator relies on) and the alternate
screen buffer (drops the live ``│`` diagnostics on exit). DECSTBM is the only
mechanism that lets a multi-line status block cohabit with append-only stderr
writes without changing the bytes the passthrough sink sees. The region is
released on every exit path — clean teardown, ``SIGINT``, ``SIGTERM``, and
``atexit`` for an unhandled exception — and re-pinned on ``SIGWINCH``.

On a non-TTY pipe the escape sequences would be visible bytes, so the
renderer is silent unless ``tty`` resolves to True (auto-detected via the
stream's ``isatty()`` by default). Signal handlers and ``atexit`` registration
are gated on a real terminal file descriptor: a test fake with ``isatty()``
True but no real ``fileno()`` exercises the ANSI emission without touching
process-global state.

Stdout contract: this layer never writes to stdout. The renderer's TextIO
is exclusively ``sys.stderr`` (or a test fake). The worker drain's
``passthrough`` sink (AgentSink in production) is fed only by
``_drain_stream``'s ``_echo`` path; ``on_step`` returns ``None`` and the
renderer's writes go to a separate file handle. The parity test in
``tests/test_swimlanes.py`` pins this: a run with the renderer active and
a run without produce byte-identical passthrough output on the same
fixture.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import signal
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

from drain_cycle.progress import fmt_elapsed as _fmt_elapsed
from drain_cycle.progress import fmt_tokens as _fmt_tokens

_log = logging.getLogger(__name__)

_DEFAULT_STALE_THRESHOLD_S = 120.0
"""Seconds since the ``_active`` marker last changed, while the run is still
live, after which the renderer treats the marker as stale — a skill likely
forgot to update or clear it. Display-only: a stale marker dims the last-known
node and logs one warning; it never blocks the run (design-doc NFR-3)."""

_DISABLE_ENV_VAR = "DRAIN_CYCLE_NO_SWIMLANES"
"""Setting this env var (to any truthy value) turns the swimlanes view off
end-to-end: the renderer becomes silent regardless of TTY, and the keyboard
listener becomes a strict no-op. Reverts the operator to today's flat
stream."""


def is_disabled() -> bool:
    """Return True when the swimlanes view is disabled via the env switch.

    Read at every construction site (not cached) so a test that sets the
    env var inside a single process sees it; the orchestrator constructs
    one renderer per issue so this is read once per drain anyway.
    """
    return bool(os.environ.get(_DISABLE_ENV_VAR))


_ANSI_DECSTBM_FMT = "\x1b[{top};{bottom}r"
"""DEC Set Top and Bottom Margins. Pins the scroll region to rows ``top``
through ``bottom`` inclusive (1-indexed). Lines outside the region are
unaffected by scrolling, which is how the owned status block at the bottom
of the terminal stays put while the passthrough above it scrolls."""

_ANSI_DECSTBM_RESET = "\x1b[r"
"""Release the scroll region — restores full-screen scrolling. Every exit
path emits this so the operator's shell returns to a normal terminal state."""

_ANSI_SAVE_CURSOR = "\x1b7"
"""DEC save-cursor (position + SGR attributes). Bracket every status repaint
with save/restore so the cursor lands back inside the scrolling region for
the next passthrough write."""

_ANSI_RESTORE_CURSOR = "\x1b8"

_ANSI_POSITION_FMT = "\x1b[{row};{col}H"
"""Absolute cursor positioning (1-indexed). Used to land on the first row of
the pinned region so every repaint overwrites the previous one in place."""

_ANSI_CLEAR_LINE = "\x1b[2K"
"""Erase the entire current line — column-position-agnostic, so the renderer
can paint at column 1 without first issuing a carriage return."""

_DEFAULT_REGION_HEIGHT = 2
"""Rows reserved at the bottom of the terminal for the status block. Two is
enough for slice 1's queue + stepper; N08/N09/N11 grow the region by raising
this. The region collapses to the smaller of the requested height and what
the terminal can give without leaving fewer than two scrolling rows."""

_DEFAULT_FALLBACK_SIZE = (80, 24)
"""Fallback ``(columns, lines)`` when ``shutil.get_terminal_size`` cannot read
the controlling terminal — pipes, test fakes, detached sessions. Matches the
``stty(1)`` default so tests get deterministic geometry without setting
``$LINES``/``$COLUMNS``."""

_QUEUE_STATES = ("done", "running", "queued")
_QUEUE_GLYPH = {"done": "✓", "running": "▶", "queued": "◯"}

_CONTROL_BYTES = "".join(chr(c) for c in range(0x20)) + "\x7f"
_CONTROL_TABLE = str.maketrans({c: "?" for c in _CONTROL_BYTES})


def _safe_label(value: str) -> str:
    """Strip control bytes (including ``\\x1b`` and ``\\r``/``\\n``) from a
    label before writing it into the redraw row.

    Skill names originate from the model's ``tool_use.input.skill`` and an
    embedded escape would let the model move the cursor, clear scrollback,
    or rewrite earlier output. Substituting each control byte with ``?``
    keeps the row width predictable without dropping characters that would
    misalign column counts."""
    return value.translate(_CONTROL_TABLE)


@dataclass(frozen=True)
class QueueItem:
    """A single issue's slot in the cycle queue.

    ``identifier`` is the human-readable Linear ID (e.g. ``"ABA-411"``).
    ``state`` is one of ``"done"``, ``"running"``, ``"queued"`` — anything
    else renders as ``"queued"`` to avoid lying about the lane state.
    """

    identifier: str
    state: str


_EXEC_STATE_FILE = "exec-state.json"
"""The pack-owned execution-state artifact (ADR 0030). The renderer reads its
top-level ``_active`` pointer for display only — no decision path imports this
reader (ADR 0032)."""


@dataclass(frozen=True)
class ActiveMarker:
    """The currently-executing step and review persona, read from the pack's
    ``_active`` pointer.

    ``step`` is the phase name the executing ``exec:*`` skill is in (e.g.
    ``"review"``). ``persona`` is the active review persona (e.g.
    ``"code-quality"``) or ``None`` outside a persona dispatch. A single string,
    last-write-wins, per ADR 0032 — never a set.
    """

    step: str
    persona: str | None


def read_active_marker(worktree_path: str | Path | None) -> ActiveMarker | None:
    """Return the ``_active`` marker from ``exec-state.json``, or ``None``.

    Display-only read across the artifact boundary (ADR 0032): the renderer
    prefers this pointer over the stream-derived step so persona depth shows on
    every worker, not just Claude Code. Returns ``None`` — never raises — when
    the worktree path is unset, the file is absent or unreadable, the JSON is
    malformed, or the ``_active`` pointer is missing or carries no string
    ``step``. Every miss is a clean fall-back to the stream path, so an old pack
    or a forgetful skill degrades rather than blocks.
    """
    if worktree_path is None:
        return None
    path = Path(worktree_path) / _EXEC_STATE_FILE
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    active = payload.get("_active")
    if not isinstance(active, dict):
        return None
    step = active.get("step")
    if not isinstance(step, str) or not step:
        return None
    persona = active.get("persona")
    if not isinstance(persona, str) or not persona:
        persona = None
    return ActiveMarker(step=step, persona=persona)


def parse_tool_use(block: Any) -> tuple[str, dict[str, Any]] | None:
    """Return ``(name, input)`` for a ``tool_use`` content block, else ``None``.

    Centralises the recognition logic the pane filter and the swimlanes
    renderer both depend on. ``name`` defaults to ``"?"`` when the block
    carries no string ``name`` field — matching today's
    ``watch_format`` rendering of malformed tool_use events — and ``input``
    defaults to ``{}`` so callers can index it without a type guard.
    """
    if not isinstance(block, dict):
        return None
    if block.get("type") != "tool_use":
        return None
    name = block.get("name")
    if not isinstance(name, str):
        name = "?"
    raw = block.get("input")
    inp = raw if isinstance(raw, dict) else {}
    return name, inp


def parse_skill_step(block: Any) -> str | None:
    """Return the skill name when ``block`` is a Skill tool_use, else ``None``.

    A step delegation in the Claude Code stream is a content block of the form ::

        {"type": "tool_use", "name": "Skill", "input": {"skill": "<name>"}}

    Any other block — text, tool_result, a Bash tool_use, a Skill block with
    missing or non-string ``input.skill`` — is not a step transition and the
    caller's active step must stay where it is. Malformed input (non-dict
    block, ``input`` not a dict, missing keys) is treated as "not a step"
    rather than raising, because the renderer must never bring down the
    worker drain it feeds off.
    """
    parsed = parse_tool_use(block)
    if parsed is None:
        return None
    name, inp = parsed
    if name != "Skill":
        return None
    skill = inp.get("skill")
    if not isinstance(skill, str) or not skill:
        return None
    return skill


class StepTracker:
    """Track the currently-active ``exec:*`` step across stream events.

    State machine: feed assistant events, get back the new step name on a
    transition (or ``None`` when the active step is unchanged or the event
    carries no Skill delegation). The tracker keeps the first-seen order of
    every skill it has observed so the renderer can draw the full stepper
    row, and re-records a re-entered step at the tail so the operator sees
    they came back through it.

    Malformed events (non-dict, missing ``message``, ``content`` not a list)
    are silently ignored — the live renderer must never fault the worker
    drain it feeds off.
    """

    def __init__(self) -> None:
        self.history: list[str] = []
        self.active: str | None = None

    def feed(self, event: Any) -> str | None:
        if not isinstance(event, dict):
            return None
        if event.get("type") != "assistant":
            return None
        message = event.get("message")
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if not isinstance(content, list):
            return None
        new_active: str | None = None
        for block in content:
            step = parse_skill_step(block)
            if step is None:
                continue
            new_active = step
        if new_active is None or new_active == self.active:
            return None
        self.active = new_active
        self.history.append(new_active)
        return new_active


class StepRenderer:
    """Draw a single-line stepper row on stderr, redrawn in place per transition.

    Wraps a :class:`StepTracker` for state and a TextIO (typically
    ``sys.stderr``) for output. On every step transition the row is
    rewritten with a leading ``\\r\\x1b[2K`` so each emit overwrites the
    previous one — the operator sees the row "flip" rather than a scrolling
    log. The active step is marked with ``▶``; prior steps are dimmed to
    ``·``.

    The renderer is silent when ``tty`` resolves to False — so a non-TTY
    pipe (CI, redirected output, test capture) emits zero bytes from this
    layer. ``tty=None`` (the default) auto-detects via the stream's
    ``isatty()``; pass ``True`` from tests to force emission to a
    StringIO. Render-path exceptions are swallowed (``OSError``, ``ValueError``)
    — this view is strictly non-gating on the worker drain that feeds it.
    """

    _ACTIVE_GLYPH = "▶"
    _PRIOR_GLYPH = "·"
    _STALE_GLYPH = "◌"

    def __init__(
        self,
        stderr: TextIO,
        tty: bool | None = None,
        worktree_path: str | Path | None = None,
        stale_threshold_s: float = _DEFAULT_STALE_THRESHOLD_S,
        region_height: int = _DEFAULT_REGION_HEIGHT,
        term_size_fn: Callable[[], tuple[int, int]] | None = None,
    ) -> None:
        self._stderr = stderr
        if is_disabled():
            tty = False
        elif tty is None:
            isatty = getattr(stderr, "isatty", None)
            try:
                tty = bool(isatty()) if callable(isatty) else False
            except Exception:
                tty = False
        self._tty = tty
        self._worktree_path = worktree_path
        self._stale_threshold_s = stale_threshold_s
        self._marker_fingerprint: tuple | None = None
        self._marker_fresh_at: float | None = None
        self._marker_stale = False
        self._stale_warned = False
        self._tracker = StepTracker()
        self._queue: list[QueueItem] = []
        self._focused: str | None = None
        self._sub_status: str = ""
        self._region_height = max(1, int(region_height))
        self._term_size_fn = term_size_fn or self._default_term_size
        self._region_active = False
        self._term_rows = 0
        self._term_cols = 0
        self._process_hooks_installed = False
        self._sigint_prev: Any = None
        self._sigterm_prev: Any = None
        self._sigwinch_prev: Any = None
        self._atexit_fn: Callable[[], None] | None = None

    @staticmethod
    def _default_term_size() -> tuple[int, int]:
        size = shutil.get_terminal_size(_DEFAULT_FALLBACK_SIZE)
        return int(size.columns), int(size.lines)

    def _is_real_tty(self) -> bool:
        """True only when ``self._stderr`` is backed by a real terminal fd.

        Tests pass an ``io.StringIO`` (or a ``_FakeStderr``) with ``isatty()``
        forced True so the ANSI-emission path runs; gating signal/atexit
        registration on a real ``fileno()`` keeps process-global state out
        of tests while still arming the production teardown path."""
        fileno = getattr(self._stderr, "fileno", None)
        if not callable(fileno):
            return False
        try:
            fd = fileno()
        except (OSError, ValueError):
            return False
        try:
            return os.isatty(int(fd))
        except (OSError, ValueError, TypeError):
            return False

    def set_queue(self, items: list[QueueItem]) -> None:
        """Install the cycle queue, sourced from the orchestrator's pick order.

        Position-in-list is the source of truth — the renderer does NOT
        re-sort by identifier, state, or any other heuristic.
        """
        self._queue = list(items)

    @property
    def queue(self) -> list[QueueItem]:
        """The current queue snapshot, in pick order. Used by the keyboard
        listener to map digit keys to identifiers."""
        return list(self._queue)

    @property
    def focused_identifier(self) -> str | None:
        """The explicitly-focused issue identifier; ``None`` means auto-follow
        the currently-running issue (the default)."""
        return self._focused

    def focus_issue(self, identifier: str | None) -> None:
        """Set the focused issue (its swimlane gets visual emphasis) or clear
        the focus with ``None`` to restore auto-follow."""
        self._focused = identifier

    @property
    def tracker(self) -> StepTracker:
        return self._tracker

    def feed(self, event: Any) -> None:
        new_step = self._tracker.feed(event)
        if new_step is None:
            return
        self._render()

    def on_progress(
        self, turns: int, cumulative_tokens: int, elapsed_seconds: float
    ) -> None:
        """Update the proof-of-life sub-status and redraw the row.

        Called by the worker once per new assistant turn (deduplicated by
        message id). The sub-status reuses the live snapshot the orchestrator
        already drives — ``turn N · X tok · 12.3s`` — so the operator can see
        the active step is still alive between step transitions.
        """
        self._sub_status = (
            f"turn {turns}"
            f" · {_fmt_tokens(cumulative_tokens)} tok"
            f" · {_fmt_elapsed(elapsed_seconds)}"
        )
        self._evaluate_staleness(elapsed_seconds)
        self._render()

    def _marker_mtime(self) -> float | None:
        if self._worktree_path is None:
            return None
        try:
            return (Path(self._worktree_path) / _EXEC_STATE_FILE).stat().st_mtime
        except OSError:
            return None

    def _evaluate_staleness(self, elapsed_seconds: float) -> None:
        """Age the marker against the run clock; flag and warn once if stale.

        Called from ``on_progress`` — so every evaluation happens while the run
        is demonstrably live (a new assistant turn just landed). A marker that
        stops changing past ``stale_threshold_s`` while turns keep advancing is
        a skill that forgot to update or clear it: dim the last-known node and
        log one warning. A marker-miss (no ``_active``) is not stale — there is
        nothing to age — so it resets the state and falls back to the stream.
        """
        marker = read_active_marker(self._worktree_path)
        if marker is None:
            self._marker_fingerprint = None
            self._marker_fresh_at = None
            self._marker_stale = False
            self._stale_warned = False
            return
        fingerprint = (self._marker_mtime(), marker.step, marker.persona)
        if fingerprint != self._marker_fingerprint:
            self._marker_fingerprint = fingerprint
            self._marker_fresh_at = elapsed_seconds
            self._marker_stale = False
            self._stale_warned = False
            return
        age = elapsed_seconds - (self._marker_fresh_at or elapsed_seconds)
        self._marker_stale = age > self._stale_threshold_s
        if self._marker_stale and not self._stale_warned:
            self._stale_warned = True
            node = (
                f"{marker.step} / {marker.persona}"
                if marker.persona
                else marker.step
            )
            _log.warning(
                "swimlanes: _active marker stale (%.0fs since last update, run "
                "still live) — showing last-known node %r dimmed; a skill may "
                "have forgotten to update or clear it.",
                age,
                node,
            )

    def _render(self) -> None:
        if not self._tty:
            return
        if not self._region_active and not self._enter_region():
            return
        stepper_row = self._render_stepper_row()
        queue_row = self._render_queue_row()
        rows = self._term_rows
        height = self._region_height
        first_row = max(1, rows - height + 1)
        try:
            self._stderr.write(_ANSI_SAVE_CURSOR)
            # Clear every reserved row before painting so collapsing a
            # two-row layout back to one doesn't leave a stale queue row.
            for r in range(first_row, rows + 1):
                self._stderr.write(_ANSI_POSITION_FMT.format(row=r, col=1))
                self._stderr.write(_ANSI_CLEAR_LINE)
            if queue_row is not None and height >= 2:
                self._stderr.write(_ANSI_POSITION_FMT.format(row=first_row, col=1))
                self._stderr.write(queue_row)
                self._stderr.write(
                    _ANSI_POSITION_FMT.format(row=first_row + 1, col=1)
                )
                self._stderr.write(stepper_row)
            else:
                # Stepper alone — paint on the last reserved row so additional
                # height (N09 footer, N11 persona depth) can land above it.
                self._stderr.write(_ANSI_POSITION_FMT.format(row=rows, col=1))
                self._stderr.write(stepper_row)
            self._stderr.write(_ANSI_RESTORE_CURSOR)
            self._stderr.flush()
        except (OSError, ValueError):
            pass

    def _enter_region(self) -> bool:
        """Pin the bottom ``region_height`` rows as the owned status block.

        Reserves the space by writing ``region_height`` newlines first so the
        existing scrollback (the orchestrator's startup table and any
        worker_event lines already on screen) scrolls up rather than getting
        overwritten by the absolute-positioned paint. Then issues DECSTBM
        with the top of the scroll region at row 1 and the bottom one row
        above the pinned block. Returns ``False`` — and stays silent — if the
        terminal is too short to keep at least two scrolling rows above the
        region, so a tiny window degrades to the flat stream rather than
        rendering on top of itself.
        """
        try:
            cols, rows = self._term_size_fn()
        except Exception:
            return False
        if rows < self._region_height + 2:
            return False
        scroll_bottom = rows - self._region_height
        try:
            self._stderr.write("\n" * self._region_height)
            self._stderr.write(
                _ANSI_DECSTBM_FMT.format(top=1, bottom=scroll_bottom)
            )
            self._stderr.write(
                _ANSI_POSITION_FMT.format(row=scroll_bottom, col=1)
            )
            self._stderr.flush()
        except (OSError, ValueError):
            return False
        self._region_active = True
        self._term_rows = rows
        self._term_cols = cols
        self._install_process_hooks()
        return True

    def _exit_region(self) -> None:
        """Release the scroll region and move the cursor below the block.

        Safe to call from a signal handler, ``atexit``, or the clean
        ``finalize`` path — re-entry is guarded by ``_region_active`` so a
        double-release on, say, SIGINT followed by ``finalize`` writes the
        reset sequence at most once.
        """
        if not self._region_active:
            return
        self._region_active = False
        try:
            self._stderr.write(_ANSI_DECSTBM_RESET)
            if self._term_rows > 0:
                self._stderr.write(
                    _ANSI_POSITION_FMT.format(row=self._term_rows, col=1)
                )
            self._stderr.write("\n")
            self._stderr.flush()
        except (OSError, ValueError):
            pass

    def _install_process_hooks(self) -> None:
        """Wire ``SIGINT``/``SIGTERM``/``SIGWINCH``/``atexit`` to the region.

        Only fires when stderr is backed by a real terminal fd — tests using
        ``io.StringIO`` (with ``isatty()`` forced True so the ANSI path runs)
        skip this so a test run does not stamp process-global handlers."""
        if self._process_hooks_installed:
            return
        if not self._is_real_tty():
            return
        try:
            self._sigint_prev = signal.signal(signal.SIGINT, self._on_signal)
            self._sigterm_prev = signal.signal(signal.SIGTERM, self._on_signal)
        except (ValueError, OSError):
            self._sigint_prev = None
            self._sigterm_prev = None
        try:
            self._sigwinch_prev = signal.signal(signal.SIGWINCH, self._on_winch)
        except (ValueError, OSError, AttributeError):
            self._sigwinch_prev = None
        self._atexit_fn = self._exit_region
        atexit.register(self._atexit_fn)
        self._process_hooks_installed = True

    def _uninstall_process_hooks(self) -> None:
        if not self._process_hooks_installed:
            return
        if self._atexit_fn is not None:
            try:
                atexit.unregister(self._atexit_fn)
            except Exception:
                pass
            self._atexit_fn = None
        for sig, prev in (
            (signal.SIGINT, self._sigint_prev),
            (signal.SIGTERM, self._sigterm_prev),
        ):
            if prev is not None:
                try:
                    signal.signal(sig, prev)
                except (ValueError, OSError):
                    pass
        if self._sigwinch_prev is not None and hasattr(signal, "SIGWINCH"):
            try:
                signal.signal(signal.SIGWINCH, self._sigwinch_prev)
            except (ValueError, OSError):
                pass
        self._sigint_prev = None
        self._sigterm_prev = None
        self._sigwinch_prev = None
        self._process_hooks_installed = False

    def _on_signal(self, signum: int, frame: Any) -> None:
        """Release the region, restore the prior handler, re-raise the signal.

        Chains to whatever handler was installed before us so the operator's
        ``KeyboardInterrupt`` (or a parent supervisor's ``SIGTERM`` handling)
        still fires — the region cleanup is additive, not substitutive."""
        self._exit_region()
        prev = (
            self._sigint_prev if signum == signal.SIGINT else self._sigterm_prev
        )
        # Mark this hook torn down before chaining so a re-entrant signal does
        # not loop through our handler twice.
        if signum == signal.SIGINT:
            self._sigint_prev = None
        else:
            self._sigterm_prev = None
        if callable(prev):
            prev(signum, frame)
        elif prev == signal.SIG_DFL:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        # SIG_IGN or None: nothing further to do.

    def _on_winch(self, signum: int, frame: Any) -> None:
        """Re-pin the region to the new terminal size, repaint the block.

        ``shutil.get_terminal_size`` re-reads the controlling tty's ``ioctl``
        on every call, so we pick up the new geometry without caching. The
        region is released and re-issued so the bottom margin moves with the
        new row count; the paint that follows lands on the new bottom rows.
        Chains to any prior handler so other ``SIGWINCH`` consumers still see
        the signal."""
        if self._region_active:
            try:
                cols, rows = self._term_size_fn()
            except Exception:
                rows, cols = self._term_rows, self._term_cols
            if rows >= self._region_height + 2 and (
                rows != self._term_rows or cols != self._term_cols
            ):
                try:
                    self._stderr.write(_ANSI_DECSTBM_RESET)
                    self._stderr.write(
                        _ANSI_DECSTBM_FMT.format(
                            top=1, bottom=rows - self._region_height
                        )
                    )
                    self._stderr.flush()
                except (OSError, ValueError):
                    pass
                self._term_rows = rows
                self._term_cols = cols
                self._render()
        prev = self._sigwinch_prev
        if callable(prev):
            try:
                prev(signum, frame)
            except Exception:
                pass

    def _effective_active(self) -> tuple[str | None, str | None]:
        """Resolve the active step and persona, marker-first.

        The pack-written ``_active`` marker wins when present — it carries
        persona depth on every worker and is rename-proof (ADR 0032). With no
        marker (old pack, or a worker that writes none), fall back to the
        stream-derived active step and a Claude-only ``None`` persona — the N02
        path. A read failure is a miss, not a fault: the view degrades to the
        stream, never blocks the run.
        """
        marker = read_active_marker(self._worktree_path)
        if marker is not None:
            return marker.step, marker.persona
        return self._tracker.active, None

    def _render_stepper_row(self) -> str:
        active_step, persona = self._effective_active()
        steps = list(self._tracker.history)
        if active_step is not None and active_step not in steps:
            steps.append(active_step)
        active_glyph = self._STALE_GLYPH if self._marker_stale else self._ACTIVE_GLYPH
        parts = []
        for step in steps:
            if step == active_step:
                label = f"{active_glyph} {_safe_label(step)}"
                if persona:
                    label = f"{label} / {_safe_label(persona)}"
            else:
                label = f"{self._PRIOR_GLYPH} {_safe_label(step)}"
            parts.append(label)
        row = " ".join(parts)
        if self._sub_status:
            row = f"{row} · {_safe_label(self._sub_status)}"
        return row

    def _render_queue_row(self) -> str | None:
        if not self._queue:
            return None
        focus = self._resolve_focus()
        parts = []
        for item in self._queue:
            state = item.state if item.state in _QUEUE_STATES else "queued"
            glyph = _QUEUE_GLYPH[state]
            ident = _safe_label(item.identifier)
            label = f"[{ident}]" if item.identifier == focus else ident
            parts.append(f"{glyph} {label}")
        return "  ".join(parts)

    def _resolve_focus(self) -> str | None:
        """Effective focus: explicit selection wins; otherwise auto-follow the
        running issue. ``None`` means the queue carries no running issue and
        no explicit focus was set."""
        if self._focused is not None:
            return self._focused
        for item in self._queue:
            if item.state == "running":
                return item.identifier
        return None

    def finalize(self) -> None:
        """Release the scroll region and unwire the process hooks.

        Called by the orchestrator after the worker stream closes — without it
        the operator's shell would stay pinned to the bottom region and the
        next log line would scroll inside it. Idempotent: a second call after
        a signal-triggered release is a no-op.
        """
        if not self._tty:
            return
        self._exit_region()
        self._uninstall_process_hooks()


def build_renderer(
    stderr: TextIO,
    *,
    worktree_path: str | Path | None = None,
    queue: list[QueueItem] | None = None,
    tty: bool | None = None,
) -> StepRenderer:
    """Construct a :class:`StepRenderer` wired to the run's worktree and queue.

    The single construction seam the orchestrator uses, so the worktree path
    (the renderer reads the ``_active`` marker from ``exec-state.json`` there)
    and the cycle queue are threaded in one place rather than at every call
    site.
    """
    renderer = StepRenderer(stderr, tty=tty, worktree_path=worktree_path)
    if queue is not None:
        renderer.set_queue(queue)
    return renderer


class KeyboardListener:
    """Raw-mode TTY keyboard watcher that routes digit keys to the renderer.

    On a TTY stdin, ``start()`` spins a daemon thread that reads stdin
    character-by-character in cbreak mode and maps digit keys ``1``–``9``
    to ``focus_issue(queue[n-1].identifier)``; ``0`` clears the focus,
    restoring auto-follow on the running issue. On a non-TTY the listener
    is a strict no-op: no thread, no termios state — the non-TTY pipe
    contract (zero ANSI, zero stdout bytes, no input handling) relies on
    this being byte-for-byte equivalent to never constructing the listener
    at all.

    ``handle_key(ch)`` is the testable seam — the live thread calls it for
    each character read; tests call it directly with synthetic input.
    """

    def __init__(self, renderer: "StepRenderer", stdin: Any | None = None) -> None:
        self._renderer = renderer
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def handle_key(self, ch: str) -> None:
        """Apply a single keystroke. Public so tests can drive the mapping
        without standing up a real PTY."""
        if not ch or not ch.isdigit():
            return
        if ch == "0":
            self._renderer.focus_issue(None)
            return
        idx = int(ch) - 1
        items = self._renderer.queue
        if 0 <= idx < len(items):
            self._renderer.focus_issue(items[idx].identifier)

    def start(self) -> None:
        """Begin the TTY listener thread. Strict no-op on a non-TTY stdin or
        when the swimlanes view is disabled via the env switch."""
        if self.active:
            return
        if is_disabled():
            return
        isatty = getattr(self._stdin, "isatty", None)
        try:
            tty_ok = bool(isatty()) if callable(isatty) else False
        except Exception:
            tty_ok = False
        if not tty_ok:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the listener thread to exit. Daemon threads die with the
        process anyway, so we don't join here — that would block teardown if
        the read() is parked inside a syscall."""
        self._stop.set()

    def _loop(self) -> None:  # pragma: no cover — exercised only on a real TTY
        try:
            import select
            import termios
            import tty as tty_mod
        except ImportError:
            return
        try:
            fd = self._stdin.fileno()
        except (AttributeError, ValueError, OSError):
            return
        try:
            old = termios.tcgetattr(fd)
        except termios.error:
            return
        try:
            tty_mod.setcbreak(fd)
            while not self._stop.is_set():
                ready, _, _ = select.select([self._stdin], [], [], 0.1)
                if not ready:
                    continue
                try:
                    ch = self._stdin.read(1)
                except (OSError, ValueError):
                    return
                if not ch:
                    return
                try:
                    self.handle_key(ch)
                except Exception:
                    pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except termios.error:
                pass
