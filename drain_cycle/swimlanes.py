"""Stream-derived swimlanes view.

Reads the same stream-json events drain-cycle already drains and surfaces the
active `exec:*` step on stderr. The parser is the single source of truth for
"is this content block a Skill delegation, and which skill" — extracted from
``watch_format`` so the pane filter and the live-output renderer agree by
construction rather than by parallel implementations drifting apart.

Redraw mechanism (OQ-4 settled): hand-rolled ANSI (carriage return +
``CSI 2K`` erase-line) emitted synchronously inside the reader thread on
every step transition. Considered and rejected: ``rich.Live``, which owns
the cursor on a refresh loop and would conflict with append-only writes to
the same stream — ``console.worker_event`` and the AgentSink-prefixed
diagnostic lines that the orchestrator and the agent already emit there.
Hand-rolled ANSI keeps the row bounded to "exactly one line at the current
cursor position" and cohabits with append-only writes by re-emitting the
row on each transition: an intervening newline-terminated log line just
moves the row to the next physical line on the next emit. The trade-off
is that on a non-TTY pipe the escape sequences would be visible bytes,
so the renderer is silent unless ``tty`` resolves to True (auto-detected
via the stream's ``isatty()`` by default; T5's fitness tests pin this).

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

from dataclasses import dataclass
from typing import Any, TextIO

_ANSI_CR_CLEAR = "\r\x1b[2K"
"""Carriage return + clear-to-EOL: rewinds the cursor to column 0 and erases
to the right, so the next write lands on top of the previous row without
appending a new line."""

_ANSI_UP_ONE = "\x1b[1A"
"""Cursor up one line: used to re-anchor the redraw region when both the
queue and stepper rows are visible."""

_QUEUE_STATES = ("done", "running", "queued")
_QUEUE_GLYPH = {"done": "✓", "running": "▶", "queued": "◯"}


@dataclass(frozen=True)
class QueueItem:
    """A single issue's slot in the cycle queue.

    ``identifier`` is the human-readable Linear ID (e.g. ``"ABA-411"``).
    ``state`` is one of ``"done"``, ``"running"``, ``"queued"`` — anything
    else renders as ``"queued"`` to avoid lying about the lane state.
    """

    identifier: str
    state: str


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

    Pre-build gate OQ-1 fixes the shape: a step delegation in the Claude Code
    stream is a content block of the form ::

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

    def __init__(self, stderr: TextIO, tty: bool | None = None) -> None:
        self._stderr = stderr
        if tty is None:
            isatty = getattr(stderr, "isatty", None)
            try:
                tty = bool(isatty()) if callable(isatty) else False
            except Exception:
                tty = False
        self._tty = tty
        self._tracker = StepTracker()
        self._queue: list[QueueItem] = []
        self._has_queue_row = False

    def set_queue(self, items: list[QueueItem]) -> None:
        """Install the cycle queue, sourced from the orchestrator's pick order.

        Position-in-list is the source of truth — the renderer does NOT
        re-sort by identifier, state, or any other heuristic (OQ-6).
        """
        self._queue = list(items)

    def mark_issue(self, identifier: str, state: str) -> None:
        """Advance one issue's lane state in place. Unknown ids are a no-op.

        Mutates the queue list to swap the matched item for one carrying
        the new state; preserves position-in-list, so the next render
        keeps the orchestrator's pick order.
        """
        for i, item in enumerate(self._queue):
            if item.identifier == identifier:
                self._queue[i] = QueueItem(identifier=identifier, state=state)
                return

    @property
    def tracker(self) -> StepTracker:
        return self._tracker

    def feed(self, event: Any) -> None:
        new_step = self._tracker.feed(event)
        if new_step is None:
            return
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

    def _render_stepper_row(self) -> str:
        parts = [
            f"{self._ACTIVE_GLYPH} {step}"
            if step == self._tracker.active
            else f"{self._PRIOR_GLYPH} {step}"
            for step in self._tracker.history
        ]
        return " ".join(parts)

    def _render_queue_row(self) -> str | None:
        if not self._queue:
            return None
        parts = []
        for item in self._queue:
            state = item.state if item.state in _QUEUE_STATES else "queued"
            glyph = _QUEUE_GLYPH[state]
            parts.append(f"{glyph} {item.identifier}")
        return "  ".join(parts)

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
