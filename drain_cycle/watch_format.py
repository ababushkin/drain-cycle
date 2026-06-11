"""Render stream-json as human-readable activity, as a stdin-to-stdout filter.

In watch mode the tmux pane runs ``claude ... | tee <fifo> | python -m
drain_cycle.watch_format``. The ``tee`` copy on the FIFO is byte-for-byte the
stream-json drain-cycle's parser reads; this filter only touches the
pane-visible copy downstream of the tee, so a formatter that crashes, buffers,
or is absent can never affect parse fidelity — the FIFO branch is independent.

Each stdin line is one stream-json event. Output is flushed per line so the
operator sees activity live rather than in buffered chunks.

Content block handling:
- ``text`` → assistant prose written as-is
- ``tool_use`` → ``→ Name(key=val, ...)`` with truncated input
- ``tool_result`` (in ``user`` events) → ``← N chars``
- unknown block types → silently skipped (forward-compat)
``result`` events → ``=== done: N turns, $X.XX ===`` footer.

A line that is not valid stream-json (or not a JSON object) is echoed
verbatim, so the worst case is the raw line the operator would have seen
anyway — never a crash, never a swallowed line.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

_INPUT_MAX = 80
"""Per-argument cap on the ``repr`` of a tool_use input value, so a large
prompt or file body renders as a short ``key='...'`` rather than flooding
the pane."""

_ANSI_RESET = "\x1b[0m"
_ANSI_DIM = "\x1b[2m"
_ANSI_ITALIC = "\x1b[3m"
_ANSI_CYAN = "\x1b[36m"
_ANSI_RED = "\x1b[31m"


class StreamFormatter:
    """Turn stream-json events into human-readable lines on ``out``.

    Turn headers are deduped by ``message.id`` — the real stream emits a turn
    once per content block (thinking, text, tool_use), each copy carrying the
    same id — so we emit one header per logical turn.

    ``color`` controls ANSI styling: ``None`` (default) auto-detects via
    ``out.isatty()``; ``True``/``False`` force on/off.
    """

    def __init__(self, out: TextIO, color: bool | None = None) -> None:
        self._out = out
        self._turn = 0
        self._last_message_id: str | None = None
        if color is None:
            isatty = getattr(out, "isatty", None)
            try:
                color = bool(isatty()) if callable(isatty) else False
            except Exception:
                color = False
        self._color = color

    def _paint(self, text: str, *codes: str) -> str:
        if not self._color or not codes:
            return text
        return "".join(codes) + text + _ANSI_RESET

    def feed(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "assistant":
            self._feed_assistant(event)
        elif event_type == "user":
            self._feed_user(event)
        elif event_type == "result":
            self._feed_result(event)

    def _feed_assistant(self, event: dict[str, Any]) -> None:
        message = event.get("message")
        if not isinstance(message, dict):
            return
        mid = message.get("id")
        if mid != self._last_message_id:
            self._last_message_id = mid
            self._turn += 1
            ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._write("\n" + self._paint(f"=== Turn {self._turn} [{ts}] ===", _ANSI_DIM))
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text", "")
                if text:
                    self._write(text)
            elif btype == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input") or {}
                args = ", ".join(
                    f"{k}={repr(v)}"[:_INPUT_MAX]
                    for k, v in (inp.items() if isinstance(inp, dict) else [])
                )
                self._write(f"→ {name}({args})")

    def _feed_user(self, event: dict[str, Any]) -> None:
        message = event.get("message")
        if not isinstance(message, dict):
            return
        content = message.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                result_content = block.get("content") or []
                if isinstance(result_content, str):
                    size = len(result_content)
                elif isinstance(result_content, list):
                    size = sum(
                        len(str(c.get("text", "")))
                        for c in result_content
                        if isinstance(c, dict)
                    )
                else:
                    size = 0
                self._write(f"← {size} chars")

    def _feed_result(self, event: dict[str, Any]) -> None:
        turns = event.get("num_turns")
        cost = event.get("total_cost_usd")
        if cost is not None:
            self._write(f"\n=== done: {turns} turns, ${cost:.2f} ===")
        else:
            self._write(f"\n=== done: {turns} turns ===")

    def _write(self, text: str) -> None:
        try:
            print(text, file=self._out, flush=True)
        except (OSError, ValueError):
            pass


def run(stdin: TextIO, stdout: TextIO, color: bool | None = None) -> int:
    """Read stream-json lines from ``stdin``, write the rendering to ``stdout``.

    Returns 0; the loop swallows malformed lines (echoing them raw) so the
    pane filter never exits non-zero on bad input. ``color`` is forwarded to
    :class:`StreamFormatter` (``None`` auto-detects via ``stdout.isatty()``).
    """
    formatter = StreamFormatter(stdout, color=color)
    for line in stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            formatter._write(stripped)
            continue
        if isinstance(event, dict):
            try:
                formatter.feed(event)
            except Exception:
                formatter._write(stripped)
        else:
            formatter._write(stripped)
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.stdin, sys.stdout))
