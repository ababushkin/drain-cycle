"""Watch-mode tmux pane + FIFO plumbing for a spawned ``claude`` session.

In watch mode the pane *is* the ``claude`` session: the orchestrator runs
``claude ... | tee <fifo> | <formatter>`` in a tmux split-pane and reads the
same stream-json bytes off the FIFO that the formatter renders human-readable
in the pane. ``tee`` sits upstream of the formatter so the FIFO branch is
byte-for-byte identical to what the worker's own subprocess pipe would carry.

This module owns the mechanics — open a pane, wire the FIFO, hand back a
readable stream and a way to kill the pane, tear everything down on failure.
The orchestrator owns the *policy*: whether to watch at all, the cross-issue
pane baton, and the fall-back-to-subprocess decision when ``open_session``
returns ``None``.
"""
from __future__ import annotations

import fcntl
import os
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

FIFO_TIMEOUT_SECONDS = 10.0
"""How long :func:`open_session` waits for the pane's ``claude | tee`` to
produce its first bytes before giving up and returning ``None`` (the caller
then falls back to a normal subprocess spawn). The pane's ``claude`` emits its
init event within seconds; this bound only guards a pane that never started
(tmux accepted the split-window but the command died), so the drain never
wedges on a dead FIFO."""


@dataclass
class WatchSession:
    """A live pane running the session plus an open stream of its stream-json.

    The pane owns the ``claude`` process; :attr:`stream` is the FIFO branch the
    orchestrator reads while the operator watches the formatted pane copy.
    :meth:`kill` stops the pane (used on a guardrail breach); :meth:`cleanup`
    removes the FIFO and its temp dir (used unconditionally once the session is
    done with). The pane itself is left for scrollback unless :meth:`kill` is
    called — the orchestrator kills it via :func:`close_pane` when the next
    issue opens its own pane.
    """

    pane_id: str
    stream: TextIO
    fifo_path: Path

    def kill(self) -> None:
        close_pane(self.pane_id)

    def cleanup(self) -> None:
        _cleanup_fifo(self.fifo_path)


def open_session(
    argv: list[str], cwd: Path, *, timeout: float = FIFO_TIMEOUT_SECONDS
) -> WatchSession | None:
    """Open a watch pane running ``argv`` and return a readable session.

    Runs ``argv | tee <fifo> | <formatter>`` in a tmux split-pane (in ``cwd``,
    the issue's worktree) and waits up to ``timeout`` for the FIFO to produce
    its first bytes. Returns a :class:`WatchSession` on success, or ``None`` on
    *any* failure — no tmux, non-zero split-window exit, a pane that never
    wrote — having already torn down whatever it partially created. The caller
    treats ``None`` as "fall back to a normal subprocess spawn."
    """
    opened = _open_pane(argv, cwd)
    if opened is None:
        return None
    pane_id, fifo_path = opened
    stream = _open_fifo_stream(fifo_path, timeout)
    if stream is None:
        # Pane accepted but produced no output in time — tear it down so the
        # caller doesn't double-spawn, then signal fall-back.
        close_pane(pane_id)
        _cleanup_fifo(fifo_path)
        return None
    return WatchSession(pane_id=pane_id, stream=stream, fifo_path=fifo_path)


def close_pane(pane_id: str) -> None:
    """Kill a tmux pane by ID; swallows all errors.

    Public because the pane_id outlives its :class:`WatchSession`: the
    orchestrator kills the prior issue's pane when the next issue opens one.
    """
    try:
        subprocess.run(
            ["tmux", "kill-pane", "-t", pane_id],
            check=False,
            capture_output=True,
        )
    except Exception:
        pass


def _formatter_stage() -> str:
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


def _open_pane(argv: list[str], cwd: Path) -> tuple[str, Path] | None:
    """Open a tmux split-pane running ``argv`` piped through ``tee`` into a FIFO.

    The pane *is* the ``claude`` session: ``argv | tee <fifo> | <formatter>``
    splits the stream — the FIFO branch carries byte-for-byte stream-json to
    drain-cycle's reader, while the pane copy flows through the formatter (see
    :func:`_formatter_stage`) so the operator sees human-readable activity
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
        + f" | {_formatter_stage()}"
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
