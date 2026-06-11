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

from drain_cycle import watch_format


def _assistant_with_content(message_id: str, content: list) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"id": message_id, "content": content},
        }
    )


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
    assert out.count("=== Turn 1") == 1
    # Assistant prose.
    assert "I will read the file." in out
    # Tool call line.
    assert "→ Read(" in out
    assert "file_path" in out
    # Tool result size (500 chars of "x").
    assert "← 500 chars" in out
    # Turn 2 header.
    assert "=== Turn 2" in out
    assert "Done." in out
    # Footer with cost.
    assert "=== done: 2 turns" in out
    assert "$0.05" in out


def test_unknown_content_types_silently_skipped() -> None:
    """Unknown content block types are skipped — no crash, no spurious
    output. ``thinking``/``text`` are known and render through."""
    out = _render(
        [
            _assistant_with_content(
                "msg_a",
                [
                    {"type": "future_unknown", "blob": "???"},
                    {"type": "text", "text": "Result."},
                ],
            ),
            _result(total_cost_usd=0.01, num_turns=1, session_id="s2", is_error=False),
        ]
    )
    assert "Result." in out
    assert "???" not in out


def test_result_without_cost_renders_turns_only() -> None:
    """A killed/partial run can emit a result with no ``total_cost_usd``; the
    footer then drops the cost segment rather than crashing on ``None``."""
    out = _render([_result(num_turns=3, session_id="s3", is_error=True)])
    assert "=== done: 3 turns ===" in out
    assert "$" not in out


def test_non_json_line_echoed_raw() -> None:
    """A line that is not stream-json is echoed verbatim — the worst-case
    fallback is the raw line the operator would have seen anyway, never a
    crash or a swallowed line."""
    out = _render(["not json at all", _result(num_turns=1)])
    assert "not json at all" in out
    assert "=== done: 1 turns ===" in out


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
    assert "=== done: 2 turns ===" in out


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
    assert "=== done: 1 turns ===" in stdout.getvalue()


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
    assert "\x1b[2m=== Turn 1" in out
    assert "===\x1b[0m" in out


def test_color_styles_each_surface() -> None:
    """With ``color=True`` each rendered surface carries its escape:
    cyan tool names, dim tool_result, red on ``is_error``, dim-italic
    thinking, dim done footer."""
    error_result = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_2",
                        "is_error": True,
                        "content": "boom",
                    }
                ]
            },
        }
    )
    out = _render(
        [
            _assistant_with_content(
                "msg_a",
                [
                    {"type": "thinking", "thinking": "musing"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Read",
                        "input": {"file_path": "/x"},
                    },
                ],
            ),
            _user_tool_result("toolu_1", "ok"),
            error_result,
            _result(total_cost_usd=0.01, num_turns=1),
        ],
        color=True,
    )
    # Thinking: dim + italic.
    assert "\x1b[2m\x1b[3mmusing\x1b[0m" in out
    # Tool name: cyan.
    assert "→ \x1b[36mRead\x1b[0m(" in out
    # Tool result: dim on the success line.
    assert "\x1b[2m← 2 chars\x1b[0m" in out
    # Tool result: red on the error line.
    assert "\x1b[31m← 4 chars\x1b[0m" in out
    # Done footer: dim.
    assert "\x1b[2m=== done: 1 turns" in out


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
    assert out.count("=== Turn 1") == 1
    assert "=== Turn 2" not in out
    assert "Hi." in out
    assert "Again." in out
