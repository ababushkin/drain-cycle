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


def _render(lines: list[str]) -> str:
    stdout = io.StringIO()
    rc = watch_format.run(io.StringIO("\n".join(lines) + "\n"), stdout)
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
    assert "=== done: 3 turns ===" in out
    assert "$" not in out


def test_non_json_line_echoed_raw() -> None:
    """A line that is not stream-json is echoed verbatim — the worst-case
    fallback is the raw line the operator would have seen anyway, never a
    crash or a swallowed line."""
    out = _render(["not json at all", _result(num_turns=1)])
    assert "not json at all" in out
    assert "=== done: 1 turns ===" in out


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
