"""Unit tests for the watch-mode stream-json formatter.

The formatter is a stdin-to-stdout filter (``drain_cycle.watch_format``): in
watch mode the pane runs ``claude ... | tee <fifo> | python -m
drain_cycle.watch_format``, so these tests drive ``run`` with canned
stream-json lines on a ``StringIO`` stdin and assert the rendering on a
``StringIO`` stdout — the same content the operator sees in the pane.

The canned ``assistant`` events deliberately repeat one ``message.id`` across
two events — the real stream-json emits a turn once per content block — so the
happy-path test pins that one turn header is emitted per logical turn.
"""
from __future__ import annotations

import io
import json

from drain_cycle import watch_format, worker


def _assistant_with_content(
    message_id: str, content: list, usage: dict | None = None
) -> str:
    message: dict = {"id": message_id, "content": content}
    if usage is not None:
        message["usage"] = usage
    return json.dumps({"type": "assistant", "message": message})


def _user_tool_result(tool_use_id: str, content_text: str) -> str:
    return json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": [{"type": "text", "text": content_text}],
                    }
                ]
            },
        }
    )


def _result(**fields: object) -> str:
    return json.dumps({"type": "result", **fields})


def _render(lines: list[str], *, color: bool | None = None) -> str:
    stdout = io.StringIO()
    rc = watch_format.run(io.StringIO("\n".join(lines) + "\n"), stdout, color=color)
    assert rc == 0
    return stdout.getvalue()


def test_text_tool_use_and_tool_result_rendered() -> None:
    """The pane rendering carries assistant prose, tool-call lines, tool-result
    sizes, turn headers (deduped by message id), and the done footer."""
    out = _render(
        [
            _assistant_with_content(
                "msg_a", [{"type": "text", "text": "I will read the file."}]
            ),
            _assistant_with_content(
                "msg_a",  # same id — second content block in same turn
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Read",
                        "input": {"file_path": "/foo.py"},
                    }
                ],
            ),
            _user_tool_result("toolu_1", "x" * 500),
            _assistant_with_content("msg_b", [{"type": "text", "text": "Done."}]),
            _result(total_cost_usd=0.05, num_turns=2, session_id="s1", is_error=False),
        ]
    )

    # Turn 1 header appears once (deduped by message_id).
    assert out.count("· Turn 1 ·") == 1
    # Assistant prose.
    assert "I will read the file." in out
    # Tool call line.
    assert "→ Read(" in out
    assert "file_path" in out
    # Tool result size (500 chars of "x").
    assert "← 500 chars" in out
    # Turn 2 header.
    assert "· Turn 2 ·" in out
    assert "Done." in out
    # Footer with cost.
    assert "· done · 2 turns" in out
    assert "$0.05" in out


def test_unknown_content_types_silently_skipped() -> None:
    """Unknown content block types (e.g. thinking) are skipped — no crash,
    no spurious output."""
    out = _render(
        [
            _assistant_with_content(
                "msg_a",
                [
                    {"type": "thinking", "thinking": "Let me think..."},
                    {"type": "text", "text": "Result."},
                ],
            ),
            _result(total_cost_usd=0.01, num_turns=1, session_id="s2", is_error=False),
        ]
    )
    assert "Result." in out
    # thinking block content must not appear
    assert "Let me think" not in out


def test_result_without_cost_renders_turns_only() -> None:
    """A killed/partial run can emit a result with no ``total_cost_usd``; the
    footer then drops the cost segment rather than crashing on ``None``."""
    out = _render([_result(num_turns=3, session_id="s3", is_error=True)])
    assert "· done · 3 turns · 0 tok · peak 0 ctx" in out
    assert "$" not in out


def test_non_json_line_echoed_raw() -> None:
    """A line that is not stream-json is echoed verbatim — the worst-case
    fallback is the raw line the operator would have seen anyway, never a
    crash or a swallowed line."""
    out = _render(["not json at all", _result(num_turns=1)])
    assert "not json at all" in out
    assert "· done · 1 turns" in out


def test_raising_render_echoes_raw_and_continues(monkeypatch) -> None:
    """If per-event rendering raises, the raw line is echoed and the loop
    keeps running — a single rendering bug cannot kill the pane filter."""
    original_feed = watch_format.StreamFormatter.feed
    calls = {"n": 0}

    def flaky_feed(self, event):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return original_feed(self, event)

    monkeypatch.setattr(watch_format.StreamFormatter, "feed", flaky_feed)

    raw = _result(num_turns=1)
    out = _render([raw, _result(num_turns=2)])
    assert raw in out
    assert "· done · 2 turns" in out


def test_tool_result_content_string_form() -> None:
    """``tool_result.content`` arrives as a plain string in some sessions; the
    size line renders as the string's length."""
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "hello world",
                    }
                ]
            },
        }
    )
    out = _render([line])
    assert "← 11 chars" in out


def test_tool_result_content_empty() -> None:
    """Missing/empty ``tool_result.content`` renders as zero chars."""
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": []}
                ]
            },
        }
    )
    out = _render([line])
    assert "← 0 chars" in out


def test_pathological_shapes_do_not_crash() -> None:
    """Malformed events (wrong types in fields the formatter touches) must
    render with rc 0 and no traceback — the filter falls back to echoing the
    raw line for any event whose render path raises."""
    lines = [
        # non-dict message
        json.dumps({"type": "assistant", "message": "not-a-dict"}),
        json.dumps({"type": "user", "message": 42}),
        # content list contains bare strings (not dicts)
        json.dumps(
            {
                "type": "assistant",
                "message": {"id": "m1", "content": ["bare", 123]},
            }
        ),
        # non-dict input on tool_use
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "id": "m2",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "X", "input": "weird"}
                    ],
                },
            }
        ),
        # string-valued usage fields (in case any future render path touches them)
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "id": "m3",
                    "content": [{"type": "text", "text": "ok"}],
                    "usage": {"input_tokens": "many", "output_tokens": "few"},
                },
            }
        ),
        _result(num_turns=1),
    ]
    stdout = io.StringIO()
    rc = watch_format.run(io.StringIO("\n".join(lines) + "\n"), stdout)
    assert rc == 0
    assert "· done · 1 turns" in stdout.getvalue()


def test_color_default_off_on_stringio() -> None:
    """Default ``color=None`` auto-detects via ``isatty()`` — a StringIO sink
    is not a tty, so no ANSI escape bytes appear in the output."""
    out = _render(
        [
            _assistant_with_content("msg_a", [{"type": "text", "text": "Hi."}]),
            _result(num_turns=1),
        ]
    )
    assert "\x1b" not in out


def test_color_true_dims_turn_header() -> None:
    """With ``color=True`` the turn header is wrapped in the dim escape and
    terminated by reset."""
    out = _render(
        [_assistant_with_content("msg_a", [{"type": "text", "text": "Hi."}])],
        color=True,
    )
    assert "\x1b[2m· Turn 1" in out
    assert "ctx\x1b[0m" in out


def test_blank_lines_skipped() -> None:
    """Blank lines between events produce no output and don't perturb turn
    counting."""
    out = _render(
        [
            _assistant_with_content("msg_a", [{"type": "text", "text": "Hi."}]),
            "",
            _assistant_with_content("msg_a", [{"type": "text", "text": "Again."}]),
        ]
    )
    assert out.count("· Turn 1 ·") == 1
    assert "· Turn 2 ·" not in out
    assert "Hi." in out
    assert "Again." in out


def _usage(inp: int, out: int, cc: int, cr: int) -> dict:
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_creation_input_tokens": cc,
        "cache_read_input_tokens": cr,
    }


def _shared_fixture_events() -> list[str]:
    """Stream with duplicate per-id events plus a mid-stream usage correction:
    msg_a appears twice (first copy underreports tokens, second copy is the
    final snapshot — last-copy-wins), msg_b appears once. Used to pin the
    pane tally against the orchestrator accumulator."""
    return [
        _assistant_with_content(
            "msg_a",
            [{"type": "text", "text": "first"}],
            usage=_usage(100, 50, 10, 20),
        ),
        _assistant_with_content(
            "msg_a",
            [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
            usage=_usage(120, 60, 10, 30),
        ),
        _assistant_with_content(
            "msg_b",
            [{"type": "text", "text": "second"}],
            usage=_usage(200, 80, 5, 150),
        ),
        _result(total_cost_usd=0.42, num_turns=2, session_id="s", is_error=False),
    ]


def test_tally_matches_worker_accumulator_on_shared_fixture() -> None:
    """The pane tally must agree with ``worker._UsageAccumulator`` on the same
    event stream — they implement the same per-id last-copy-wins arithmetic,
    so cumulative and peak-context are pinned equal."""
    events = _shared_fixture_events()

    formatter = watch_format.StreamFormatter(io.StringIO(), color=False)
    for line in events:
        formatter.feed(json.loads(line))

    acc = worker._UsageAccumulator()
    for line in events:
        acc.feed(json.loads(line))
    acc_usage = acc.usage()

    assert formatter._tally.cumulative() == acc_usage["cumulative"]
    assert formatter._tally.peak_context() == acc_usage["peak_context"]


def test_footer_renders_cumulative_and_peak() -> None:
    """The footer carries cumulative tokens and peak-context, both
    formatted via ``fmt_tokens``."""
    out = _render(_shared_fixture_events())
    # cumulative = 200+80+5+150 + 120+60+10+30 = 655
    # peak ctx (msg_b) = 200 + 150 + 5 = 355
    assert "· done · 2 turns · $0.42 · 655 tok · peak 355 ctx" in out
