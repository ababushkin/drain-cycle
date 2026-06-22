"""Stream-derived swimlanes view.

Reads the same stream-json events drain-cycle already drains and surfaces the
active `exec:*` step on stderr. The parser is the single source of truth for
"is this content block a Skill delegation, and which skill" — extracted from
``watch_format`` so the pane filter and the live-output renderer agree by
construction rather than by parallel implementations drifting apart.

Redraw mechanism: hand-rolled ANSI (carriage return + ``CSI 2K`` erase-line)
emitted synchronously inside the reader thread on every step transition.
Rejected ``rich.Live``: it owns the cursor on a refresh loop and would
conflict with the append-only writes to the same stream that
``console.worker_event`` and the AgentSink-prefixed diagnostic lines emit.
Hand-rolled ANSI keeps the row bounded to "exactly one line at the current
cursor position" and cohabits with append-only writes by re-emitting the
row on each transition: an intervening newline-terminated log line just
moves the row to the next physical line on the next emit. On a non-TTY
pipe the escape sequences would be visible bytes, so the renderer is
silent unless ``tty`` resolves to True (auto-detected via the stream's
``isatty()`` by default).

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

import json
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from drain_cycle.progress import fmt_elapsed as _fmt_elapsed
from drain_cycle.progress import fmt_tokens as _fmt_tokens

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


_ANSI_CR_CLEAR = "\r\x1b[2K"
"""Carriage return + clear-to-EOL: rewinds the cursor to column 0 and erases
to the right, so the next write lands on top of the previous row without
appending a new line."""

_ANSI_UP_ONE = "\x1b[1A"
"""Cursor up one line: used to re-anchor the redraw region when both the
queue and stepper rows are visible."""

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

    def __init__(
        self,
        stderr: TextIO,
        tty: bool | None = None,
        worktree_path: str | Path | None = None,
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
        self._tracker = StepTracker()
        self._queue: list[QueueItem] = []
        self._has_queue_row = False
        self._focused: str | None = None
        self._sub_status: str = ""

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
        self._render()

    def _render(self) -> None:
        if not self._tty:
            return
        stepper_row = self._render_stepper_row()
        queue_row = self._render_queue_row()
        try:
            if queue_row is None:
                # Single-row layout: stepper only, redraw in place.
                self._stderr.write(f"{_ANSI_CR_CLEAR}{stepper_row}")
            else:
                # Two-row layout: queue above stepper. On the first emit, draw
                # both rows; on subsequent emits, move the cursor up one line
                # to re-anchor on the queue row before rewriting both.
                if self._has_queue_row:
                    self._stderr.write(_ANSI_UP_ONE)
                self._stderr.write(
                    f"{_ANSI_CR_CLEAR}{queue_row}\n{_ANSI_CR_CLEAR}{stepper_row}"
                )
                self._has_queue_row = True
            self._stderr.flush()
        except (OSError, ValueError):
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
        parts = []
        for step in steps:
            if step == active_step:
                label = f"{self._ACTIVE_GLYPH} {_safe_label(step)}"
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
        """Write a terminating newline so subsequent stderr writes start fresh.

        Called by the worker after the stream closes — without it, the next
        log line would land on top of the stepper row.
        """
        if not self._tty:
            return
        try:
            self._stderr.write("\n")
            self._stderr.flush()
        except (OSError, ValueError):
            pass


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
