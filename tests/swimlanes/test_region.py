"""Owned-region invariants for the swimlanes renderer.

The richer status view pins the bottom rows of the terminal with a DEC
scroll region (DECSTBM). These tests pin the contracts the rest of the
view builds on:

* The region opens on the first paint with explicit numeric DECSTBM bounds
  and reserves space by pushing existing scrollback up.
* Every paint lands on absolute rows inside the pinned block, bracketed by
  DEC save/restore cursor so the next passthrough write lands in the
  scrolling area.
* The region is released on every exit path — clean finalize, ``SIGINT``,
  ``SIGTERM``, and an ``atexit``-style direct release — leaving the
  terminal with no residual region.
* A ``SIGWINCH`` re-pins the region to the new terminal size and repaints
  the block in place.
* A terminal too short to keep at least two scrolling rows above the region
  degrades to silence rather than rendering on top of itself.
* A write fault mid-paint is swallowed — the renderer never propagates an
  exception into the worker drain that feeds it.
"""
from __future__ import annotations

import io
import signal
from typing import Any

from drain_cycle import swimlanes


def _assistant_skill_event(message_id: str, skill: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "content": [
                {"type": "tool_use", "name": "Skill", "input": {"skill": skill}},
            ],
        },
    }


def _make_renderer(
    *, rows: int = 24, cols: int = 80, region_height: int = 2
) -> tuple[io.StringIO, swimlanes.StepRenderer]:
    err = io.StringIO()
    renderer = swimlanes.StepRenderer(
        err,
        tty=True,
        region_height=region_height,
        term_size_fn=lambda: (cols, rows),
    )
    return err, renderer


def test_region_opens_with_decstbm_and_reserves_space_for_pinned_rows():
    err, renderer = _make_renderer(rows=24, region_height=2)
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    out = err.getvalue()
    # Region reservation: two newlines pushed first so existing scrollback
    # moves up by the region height before DECSTBM is issued.
    assert out.startswith("\n\n")
    # DECSTBM with explicit bounds: scroll region is rows 1..22 on a 24-row
    # terminal with a 2-row pinned block.
    assert "\x1b[1;22r" in out
    # Cursor is moved into the scrolling area (row 22) so the next
    # passthrough write lands above the region.
    assert "\x1b[22;1H" in out


def test_region_paints_pinned_rows_with_absolute_positioning_and_save_restore():
    err, renderer = _make_renderer(rows=24, region_height=2)
    renderer.set_queue(
        [
            swimlanes.QueueItem("ABA-410", "running"),
            swimlanes.QueueItem("ABA-411", "queued"),
        ]
    )
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    out = err.getvalue()
    # Bracket every paint with DEC save/restore so the cursor returns to the
    # scrolling area between repaints.
    assert "\x1b7" in out
    assert "\x1b8" in out
    # Queue on row 23, stepper on row 24 (the bottom of the terminal).
    assert "\x1b[23;1H" in out
    assert "\x1b[24;1H" in out


def test_region_clears_reserved_rows_each_repaint():
    """A repaint clears every row in the region first so a collapse from
    two-row to one-row (or a shorter step name) does not leave stale text
    in the pinned block."""
    err, renderer = _make_renderer(rows=24, region_height=2)
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    err.truncate(0)
    err.seek(0)
    renderer.feed(_assistant_skill_event("m2", "exec:breakdown"))
    out = err.getvalue()
    # Erase line on both reserved rows before painting the new content.
    assert out.count("\x1b[2K") >= 2


def test_finalize_releases_region_and_moves_cursor_below_block():
    err, renderer = _make_renderer(rows=24, region_height=2)
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    err.truncate(0)
    err.seek(0)
    renderer.finalize()
    out = err.getvalue()
    # DECSTBM reset releases the region.
    assert "\x1b[r" in out
    # Cursor lands on the bottom row so the next stderr write starts fresh
    # below the (now released) status block.
    assert "\x1b[24;1H" in out
    assert out.endswith("\n")


def test_finalize_is_idempotent_when_region_never_opened():
    """A finalize before any paint must not emit a stray DECSTBM reset —
    the region was never opened, so there is nothing to release."""
    err, renderer = _make_renderer(rows=24, region_height=2)
    renderer.finalize()
    assert err.getvalue() == ""


def test_finalize_is_idempotent_after_signal_release():
    """A signal handler that already released the region must not cause
    finalize to write a second DECSTBM reset — re-entry is guarded."""
    err, renderer = _make_renderer(rows=24, region_height=2)
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    # Simulate the signal path having already released the region.
    renderer._exit_region()
    err.truncate(0)
    err.seek(0)
    renderer.finalize()
    assert err.getvalue() == ""


def test_sigint_handler_releases_region_and_chains_to_previous():
    """SIGINT during a live render must release the region (so the
    operator's shell is left clean) and chain to the previous handler
    (so the operator still gets KeyboardInterrupt or the prior trap)."""
    err, renderer = _make_renderer(rows=24, region_height=2)
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    err.truncate(0)
    err.seek(0)
    chained: list[tuple[int, Any]] = []

    def prev_handler(signum: int, frame: Any) -> None:
        chained.append((signum, frame))

    renderer._sigint_prev = prev_handler
    renderer._on_signal(signal.SIGINT, None)
    out = err.getvalue()
    assert "\x1b[r" in out, "SIGINT must release the scroll region"
    assert chained == [(signal.SIGINT, None)], (
        "SIGINT must chain to the prior handler so KeyboardInterrupt still fires"
    )


def test_sigterm_handler_releases_region_and_chains_to_previous():
    err, renderer = _make_renderer(rows=24, region_height=2)
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    err.truncate(0)
    err.seek(0)
    chained: list[int] = []

    def prev_handler(signum: int, frame: Any) -> None:
        chained.append(signum)

    renderer._sigterm_prev = prev_handler
    renderer._on_signal(signal.SIGTERM, None)
    out = err.getvalue()
    assert "\x1b[r" in out, "SIGTERM must release the scroll region"
    assert chained == [signal.SIGTERM]


def test_signal_handler_safe_when_no_previous_handler_installed():
    """When the prior handler is ``signal.SIG_IGN`` or ``None``, the
    release path must still run — and not raise — even though there is
    nothing to chain to."""
    err, renderer = _make_renderer(rows=24, region_height=2)
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    err.truncate(0)
    err.seek(0)
    renderer._sigint_prev = signal.SIG_IGN
    renderer._on_signal(signal.SIGINT, None)
    assert "\x1b[r" in err.getvalue()


def test_sigwinch_repins_region_on_resize_and_repaints():
    rows = [24]

    def term_size() -> tuple[int, int]:
        return 80, rows[0]

    err = io.StringIO()
    renderer = swimlanes.StepRenderer(
        err, tty=True, region_height=2, term_size_fn=term_size
    )
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    err.truncate(0)
    err.seek(0)
    # Resize the terminal to 30 rows and dispatch the SIGWINCH handler.
    rows[0] = 30
    renderer._on_winch(signal.SIGWINCH, None)
    out = err.getvalue()
    # Region released and re-issued with the new bottom margin (rows-2 = 28).
    assert "\x1b[r" in out
    assert "\x1b[1;28r" in out
    # Repaint lands on the new bottom row.
    assert "\x1b[30;1H" in out


def test_sigwinch_noop_when_terminal_size_unchanged():
    """An ioctl(2) that returns the same size — common on a tab change in
    some terminals — must not cause a redundant region re-issue."""
    err, renderer = _make_renderer(rows=24, region_height=2)
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    err.truncate(0)
    err.seek(0)
    renderer._on_winch(signal.SIGWINCH, None)
    assert err.getvalue() == ""


def test_sigwinch_chains_to_previous_handler():
    err, renderer = _make_renderer(rows=24, region_height=2)
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    seen: list[int] = []

    def prev_handler(signum: int, frame: Any) -> None:
        seen.append(signum)

    renderer._sigwinch_prev = prev_handler
    renderer._on_winch(signal.SIGWINCH, None)
    assert seen == [signal.SIGWINCH]


def test_region_is_silent_when_terminal_too_small():
    """A terminal that cannot keep at least two scrolling rows above the
    pinned region degrades to silence — the operator sees a flat stream
    instead of a status block rendering on top of itself."""
    err, renderer = _make_renderer(rows=3, region_height=2)
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    # Region never opened, so no bytes written.
    assert err.getvalue() == ""


def test_region_height_three_paints_a_three_row_block(tmp_path):
    """A larger ``region_height`` widens the pinned block — the surface a
    future footer or persona drill-down fills. Pinning the contract here
    means a downstream caller raising the height only has to fill the
    extra rows."""
    err, renderer = _make_renderer(rows=24, region_height=3)
    renderer.set_queue([swimlanes.QueueItem("ABA-410", "running")])
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    out = err.getvalue()
    # DECSTBM bottom margin moves up to row 21 on a 24-row terminal with a
    # 3-row pinned block.
    assert "\x1b[1;21r" in out
    # The pinned block spans rows 22, 23, 24.
    for row in (22, 23, 24):
        assert f"\x1b[{row};1H" in out


class _FaultyStderr:
    """A TextIO that writes normally until the configured trigger fires,
    then raises ``OSError`` from every subsequent write.

    Drives the NFR-3 fault-injection contract for the multi-line region:
    a partial paint (DECSTBM opened, absolute-positioning writes started)
    must not propagate the fault into the worker drain that called
    ``feed``."""

    def __init__(self, fail_after: int) -> None:
        self._buf: list[str] = []
        self._writes = 0
        self._fail_after = fail_after

    def isatty(self) -> bool:
        return True

    def write(self, s: str) -> int:
        self._writes += 1
        if self._writes > self._fail_after:
            raise OSError("simulated stderr failure")
        self._buf.append(s)
        return len(s)

    def flush(self) -> None:
        if self._writes > self._fail_after:
            raise OSError("simulated stderr failure")

    def getvalue(self) -> str:
        return "".join(self._buf)


def test_multi_line_region_swallows_write_fault_mid_paint():
    """NFR-3 for the multi-line region: a write failure inside the
    absolute-positioning paint must not raise out of ``feed`` — the
    renderer is non-gating on the worker drain. The exit code that would
    have come out of a clean run is therefore preserved."""
    err = _FaultyStderr(fail_after=5)
    renderer = swimlanes.StepRenderer(
        err, tty=True, region_height=2, term_size_fn=lambda: (80, 24)
    )
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    # A second emit lands further into the paint sequence — also swallowed.
    renderer.feed(_assistant_skill_event("m2", "exec:breakdown"))
    # And the finalize path tolerates the fault too.
    renderer.finalize()


def test_multi_line_region_swallows_fault_when_entering_region():
    """A failure on the very first newline of region entry must keep the
    region inactive — and never raise — so a tiny but TTY-claiming sink
    degrades to the flat stream rather than half-opening a region the
    teardown path cannot release."""
    err = _FaultyStderr(fail_after=0)
    renderer = swimlanes.StepRenderer(
        err, tty=True, region_height=2, term_size_fn=lambda: (80, 24)
    )
    renderer.feed(_assistant_skill_event("m1", "exec:pickup"))
    assert renderer._region_active is False, (
        "a failure during region entry must leave the region inactive so "
        "_exit_region does not emit a release for a region that never opened"
    )
