"""Swimlanes fitness tests — non-gating / clean-degradation contracts (T5).

These tests pin the four contracts the swimlanes view must keep regardless
of the operator's terminal or environment:

* **Disable switch (OQ-5)** — setting ``DRAIN_CYCLE_NO_SWIMLANES`` reverts
  the operator to today's flat stream. Golden output: a drain run with the
  switch set is byte-identical (on stderr from the swimlanes layer, and
  on the worker drain's passthrough sink) to a drain run that never
  constructs the swimlanes objects at all.
* **Non-TTY pipe (NFR-4)** — when stderr is a pipe (``isatty()`` False),
  the swimlanes layer emits zero ANSI escapes and zero stdout bytes. The
  ``StepRenderer.tty=False`` path is silent by construction; this test
  pins the contract end-to-end against a fake stream.
* **Render-path exception (NFR-3)** — a swimlanes ``on_step`` callback
  that raises mid-stream does not change ``_UsageAccumulator`` state, does
  not abort the drain, and would leave a real ``WorkerResult`` and exit
  code identical to a clean run. The worker swallows ``on_step``
  exceptions; this test pins it against ``_drain_stream`` directly.
* **Usage parity (NFR-2)** — ``_UsageAccumulator`` totals are identical
  feature-on vs feature-off. The on/off switch is the swimlanes
  ``on_step`` callback being installed or not; the accumulator is fed
  unconditionally upstream of it.
"""
from __future__ import annotations

import io
import json
import re

import pytest

from drain_cycle import swimlanes, worker

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
"""CSI / SGR escape sequence matcher. Any match in stderr from the swimlanes
layer on a non-TTY stream is a contract violation."""


def _assistant_skill_event(message_id: str, skill: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            "content": [
                {"type": "tool_use", "name": "Skill", "input": {"skill": skill}},
            ],
        },
    }


def _result_event() -> dict:
    return {
        "type": "result",
        "total_cost_usd": 0.42,
        "num_turns": 2,
        "session_id": "sess-1",
        "is_error": False,
    }


def _fixture_stream() -> io.StringIO:
    """A stream-json fixture covering: two assistant turns with Skill
    delegations (so the swimlanes layer would transition twice), one
    non-JSON diagnostic line (so the passthrough sink gets exercised),
    and a final result event (so the accumulator's session-summary
    fields are populated)."""
    lines = [
        json.dumps(_assistant_skill_event("m1", "exec:pickup")),
        "[hook] non-json warning, sink-echoed",
        json.dumps(_assistant_skill_event("m2", "exec:build")),
        json.dumps(_result_event()),
    ]
    return io.StringIO("\n".join(lines) + "\n")


def _drain(on_step) -> tuple[worker._UsageAccumulator, str]:
    """Run ``_drain_stream`` on the fixture with the given ``on_step``
    callback and return ``(accumulator, sink_text)``. Compares two runs
    pinpoints what the swimlanes layer is — and isn't — doing."""
    acc = worker._UsageAccumulator()
    sink = io.StringIO()
    worker._drain_stream(_fixture_stream(), acc, sink, None, on_step)
    return acc, sink.getvalue()


class _FakeStderr(io.StringIO):
    """A StringIO with an explicit ``isatty()`` so renderer auto-detection
    sees it as a pipe — pinning the non-TTY contract."""

    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_disable_switch_reverts_to_flat_stream_golden_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Golden-output test: with ``DRAIN_CYCLE_NO_SWIMLANES`` set, the renderer
    is silent on a TTY stream and the worker drain's passthrough sink is
    byte-identical to a baseline run that never constructed the renderer.

    Pins the disable contract end-to-end: nothing in the live drain output
    differs between feature-disabled and feature-uninstalled.
    """
    monkeypatch.setenv(swimlanes._DISABLE_ENV_VAR, "1")

    err = _FakeStderr(tty=True)
    renderer = swimlanes.StepRenderer(err)
    # Even though stderr.isatty() is True, the disable switch forces the
    # renderer silent — no carriage-return-clear, no row, no bytes.
    acc_disabled, sink_disabled = _drain(renderer.feed)
    renderer.finalize()

    assert err.getvalue() == "", (
        "disable switch must keep the swimlanes layer silent on stderr; "
        f"got {err.getvalue()!r}"
    )

    # The keyboard listener must also no-op when disabled — even on a TTY stdin.
    class FakeTtyStdin:
        def isatty(self) -> bool:
            return True

    listener = swimlanes.KeyboardListener(renderer, stdin=FakeTtyStdin())
    listener.start()
    assert listener.active is False, (
        "disable switch must prevent the keyboard listener from spawning "
        "a thread, even on a TTY stdin"
    )

    # Baseline: a feature-uninstalled run produces the same sink output.
    acc_baseline, sink_baseline = _drain(on_step=None)
    assert sink_disabled == sink_baseline, (
        "disable switch must leave the worker drain's passthrough sink "
        "byte-identical to a feature-uninstalled run"
    )


def test_non_tty_pipe_emits_zero_ansi_and_zero_stdout_bytes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """NFR-4: when stderr is a pipe (``isatty()`` False), the renderer emits
    zero ANSI escapes — no carriage-return-clear, no cursor-up, no SGR —
    and the swimlanes layer touches stdout zero times.
    """
    err = _FakeStderr(tty=False)
    renderer = swimlanes.StepRenderer(err)
    # Install a queue so the queue-row path is also exercised.
    renderer.set_queue(
        [
            swimlanes.QueueItem("ABA-410", "running"),
            swimlanes.QueueItem("ABA-411", "queued"),
        ]
    )

    acc, _sink = _drain(renderer.feed)
    renderer.finalize()

    err_text = err.getvalue()
    assert err_text == "", (
        f"non-TTY pipe must emit zero swimlanes bytes on stderr; got {err_text!r}"
    )
    assert not _ANSI_RE.search(err_text), (
        "non-TTY pipe must emit zero ANSI escapes"
    )
    captured = capsys.readouterr()
    assert captured.out == "", (
        f"swimlanes layer must never touch stdout; got {captured.out!r}"
    )


def test_render_path_exception_leaves_accumulator_state_identical_to_clean_run() -> None:
    """NFR-3: an injected ``on_step`` exception mid-stream does not abort
    ``_drain_stream`` and does not change the accumulator's final state.

    The worker swallows ``on_step`` exceptions by design (so the live view
    can never fault the worker drain it feeds off); this test pins that
    behaviour against the same fixture used by the other fitness tests.
    A real ``WorkerResult`` is built from this accumulator and the process
    exit code — identical accumulator state means identical result fields.
    """

    def faulting_on_step(_event: dict) -> None:
        raise RuntimeError("render-path simulated fault")

    acc_faulting, sink_faulting = _drain(faulting_on_step)
    acc_clean, sink_clean = _drain(on_step=None)

    # Token totals, session summary, and turn count survive the fault.
    assert acc_faulting.cumulative() == acc_clean.cumulative()
    assert acc_faulting.live_snapshot() == acc_clean.live_snapshot()
    assert acc_faulting.cost_usd == acc_clean.cost_usd
    assert acc_faulting.num_turns == acc_clean.num_turns
    assert acc_faulting.session_id == acc_clean.session_id
    assert acc_faulting.is_error == acc_clean.is_error
    # The passthrough sink also survives — the non-JSON diagnostic line
    # is echoed exactly once, whether or not on_step raises.
    assert sink_faulting == sink_clean


def test_usage_accumulator_totals_are_identical_feature_on_vs_off() -> None:
    """NFR-2: enabling the swimlanes layer (an installed ``on_step``)
    must not perturb the usage accumulator's state — the accumulator is
    fed by ``_drain_stream`` unconditionally, upstream of ``on_step``.

    This is the strongest guarantee the orchestrator's token-cap monitor
    relies on: turning the view on cannot, by construction, change the
    billed-token total a per-issue cap is compared against.
    """
    err = _FakeStderr(tty=True)
    renderer = swimlanes.StepRenderer(err)

    acc_on, sink_on = _drain(renderer.feed)
    acc_off, sink_off = _drain(on_step=None)

    assert acc_on.cumulative() == acc_off.cumulative()
    assert acc_on.live_snapshot() == acc_off.live_snapshot()
    assert acc_on.cost_usd == acc_off.cost_usd
    assert acc_on.num_turns == acc_off.num_turns
    assert acc_on.session_id == acc_off.session_id
    assert acc_on.is_error == acc_off.is_error
    # The sink (passthrough) is also untouched — the swimlanes layer does
    # not consume or mutate the non-JSON diagnostic stream.
    assert sink_on == sink_off
