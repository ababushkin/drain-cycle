"""Tests for ``drain_cycle.handoff``.

``read`` must never raise: a missing file, a truncated JSON write, or a
structurally wrong payload all return ``None``. A well-formed file returns
a typed ``HandoffData`` object listing the submitted PRs. An empty
``pr_urls`` list reads as ``None`` — the skill writes URLs only after a PR
is actually created, so "present but empty" means submission never happened.
"""
from __future__ import annotations

import json
from pathlib import Path

from drain_cycle.handoff import EXEC_STATE_FILE, HANDOFF_FILE, HandoffData, PullRequest, read, read_partial, write


def _valid_data() -> HandoffData:
    return HandoffData(
        pr_urls=(
            PullRequest(title="feat: crash-proof filter", url="https://github.com/o/r/pull/1"),
            PullRequest(title="feat: transcript core", url="https://github.com/o/r/pull/2"),
        )
    )


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    data = _valid_data()
    write(tmp_path, data)
    result = read(tmp_path)

    assert result is not None
    assert result.pr_urls == data.pr_urls


def test_read_missing_file_returns_none(tmp_path: Path) -> None:
    assert read(tmp_path) is None


def test_read_malformed_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text("not valid json{{{")
    assert read(tmp_path) is None


def test_read_wrong_top_level_type_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(json.dumps([1, 2, 3]))
    assert read(tmp_path) is None


def test_read_missing_pr_urls_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(json.dumps({"other": "key"}))
    assert read(tmp_path) is None


def test_read_empty_pr_urls_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(json.dumps({"pr_urls": []}))
    assert read(tmp_path) is None


def test_read_pr_urls_not_list_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(json.dumps({"pr_urls": {"title": "x", "url": "y"}}))
    assert read(tmp_path) is None


def test_read_entry_not_dict_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(json.dumps({"pr_urls": ["just a string"]}))
    assert read(tmp_path) is None


def test_read_entry_missing_url_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_urls": [{"title": "no url here"}]})
    )
    assert read(tmp_path) is None


def test_read_entry_empty_url_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_urls": [{"title": "t", "url": ""}]})
    )
    assert read(tmp_path) is None


def test_read_entry_title_not_string_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_urls": [{"title": 42, "url": "https://x/pull/1"}]})
    )
    assert read(tmp_path) is None


def test_read_entry_url_not_string_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_urls": [{"title": "t", "url": 42}]})
    )
    assert read(tmp_path) is None


def test_read_entry_missing_title_returns_none(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_urls": [{"url": "https://x/pull/1"}]})
    )
    assert read(tmp_path) is None


def test_read_entry_empty_title_is_accepted(tmp_path: Path) -> None:
    # title is informational; url is the canonical identity. An empty title is
    # accepted on purpose (unlike an empty url, which invalidates the entry).
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_urls": [{"title": "", "url": "https://x/pull/1"}]})
    )
    result = read(tmp_path)
    assert result is not None
    assert result.pr_urls == (PullRequest(title="", url="https://x/pull/1"),)


def test_read_mixed_valid_and_invalid_entries_returns_none(tmp_path: Path) -> None:
    # One good PR followed by a malformed one (e.g. a partial write): the list
    # is all-or-nothing, so a single bad entry invalidates the whole handoff.
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps(
            {
                "pr_urls": [
                    {"title": "good", "url": "https://x/pull/1"},
                    {"title": "bad", "url": ""},
                ]
            }
        )
    )
    assert read(tmp_path) is None


def test_write_creates_file_at_expected_path(tmp_path: Path) -> None:
    write(tmp_path, _valid_data())
    assert (tmp_path / HANDOFF_FILE).exists()


def test_read_never_raises_on_empty_file(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text("")
    assert read(tmp_path) is None


# --- schema-v2 verdict fields ---


def test_write_then_read_includes_verdicts(tmp_path: Path) -> None:
    ov = {"result": "pass", "findings": [], "invoked_at": "2026-01-01T00:00:00Z"}
    pv = {"result": "ok", "route": "auto-merge", "reasoning": "looks good"}
    data = HandoffData(
        pr_urls=_valid_data().pr_urls,
        outcome_verdict=ov,
        prep_verdict=pv,
    )
    write(tmp_path, data)
    result = read(tmp_path)
    assert result is not None
    assert result.outcome_verdict == ov
    assert result.prep_verdict == pv


def test_read_verdict_fields_are_optional(tmp_path: Path) -> None:
    write(tmp_path, _valid_data())  # no verdicts
    result = read(tmp_path)
    assert result is not None
    assert result.outcome_verdict is None
    assert result.prep_verdict is None


def test_read_ignores_non_dict_verdict(tmp_path: Path) -> None:
    payload = {
        "pr_urls": [{"title": "t", "url": "https://x/pull/1"}],
        "outcome_verdict": "not-a-dict",
        "prep_verdict": 42,
    }
    (tmp_path / HANDOFF_FILE).write_text(json.dumps(payload))
    result = read(tmp_path)
    assert result is not None
    assert result.outcome_verdict is None
    assert result.prep_verdict is None


def test_read_drops_verdict_dict_missing_result(tmp_path: Path) -> None:
    # A verdict must carry a ``result`` key; downstream code reads it directly.
    # A dict without it is not a usable verdict and is dropped to None rather
    # than passed on to crash a reader that assumes the key is present.
    payload = {
        "pr_urls": [{"title": "t", "url": "https://x/pull/1"}],
        "outcome_verdict": {"findings": []},
        "prep_verdict": {"route": "auto-merge"},
    }
    (tmp_path / HANDOFF_FILE).write_text(json.dumps(payload))
    result = read(tmp_path)
    assert result is not None
    assert result.outcome_verdict is None
    assert result.prep_verdict is None


# --- read_partial ---


def test_read_partial_drops_verdict_dict_missing_result(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"outcome_verdict": {"findings": []}, "prep_verdict": {}})
    )
    assert read_partial(tmp_path) == (None, None)


def test_read_partial_returns_verdicts_with_empty_pr_urls(tmp_path: Path) -> None:
    # The actual halt file: the skill ran, submitted nothing (``pr_urls`` present
    # but empty, so ``read`` returns None), yet recorded a fail verdict.
    # ``read_partial`` must still surface that verdict.
    ov = {"result": "fail", "findings": ["nothing submitted"]}
    pv = {"result": "blocked", "route": "human-review", "reasoning": "no PRs"}
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_urls": [], "outcome_verdict": ov, "prep_verdict": pv})
    )
    assert read(tmp_path) is None
    got_ov, got_pv = read_partial(tmp_path)
    assert got_ov == ov
    assert got_pv == pv


def test_read_partial_returns_verdicts_without_pr_urls(tmp_path: Path) -> None:
    ov = {"result": "pass", "findings": []}
    pv = {"result": "ok", "route": "auto-merge", "reasoning": "fine"}
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"outcome_verdict": ov, "prep_verdict": pv})
    )
    got_ov, got_pv = read_partial(tmp_path)
    assert got_ov == ov
    assert got_pv == pv


def test_read_partial_returns_none_none_on_missing_file(tmp_path: Path) -> None:
    assert read_partial(tmp_path) == (None, None)


def test_read_partial_returns_none_none_on_malformed_json(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text("not json{{{")
    assert read_partial(tmp_path) == (None, None)


def test_read_partial_returns_none_none_on_non_dict_payload(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(json.dumps([1, 2, 3]))
    assert read_partial(tmp_path) == (None, None)


def test_read_partial_reads_verdicts_from_complete_handoff(tmp_path: Path) -> None:
    ov = {"result": "fail", "findings": ["f1"]}
    data = HandoffData(pr_urls=_valid_data().pr_urls, outcome_verdict=ov)
    write(tmp_path, data)
    got_ov, got_pv = read_partial(tmp_path)
    assert got_ov == ov
    assert got_pv is None


# --- exec-state.json dual-read ---


def _valid_exec_state_payload() -> dict:
    return {
        "pr_urls": [
            {"title": "feat: crash-proof filter", "url": "https://github.com/o/r/pull/1"},
        ]
    }


def test_read_prefers_exec_state_file_over_legacy(tmp_path: Path) -> None:
    exec_payload = {
        "pr_urls": [{"title": "from exec-state", "url": "https://github.com/o/r/pull/10"}]
    }
    legacy_payload = {
        "pr_urls": [{"title": "from legacy", "url": "https://github.com/o/r/pull/20"}]
    }
    (tmp_path / EXEC_STATE_FILE).write_text(json.dumps(exec_payload))
    (tmp_path / HANDOFF_FILE).write_text(json.dumps(legacy_payload))

    result = read(tmp_path)
    assert result is not None
    assert result.pr_urls[0].title == "from exec-state"
    assert result.pr_urls[0].url == "https://github.com/o/r/pull/10"


def test_read_falls_back_to_legacy_when_exec_state_absent(tmp_path: Path) -> None:
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_urls": [{"title": "legacy", "url": "https://github.com/o/r/pull/5"}]})
    )
    result = read(tmp_path)
    assert result is not None
    assert result.pr_urls[0].title == "legacy"


def test_read_falls_back_to_legacy_when_exec_state_invalid(tmp_path: Path) -> None:
    (tmp_path / EXEC_STATE_FILE).write_text("not valid json{{{")
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_urls": [{"title": "legacy", "url": "https://github.com/o/r/pull/5"}]})
    )
    result = read(tmp_path)
    assert result is not None
    assert result.pr_urls[0].title == "legacy"


def test_read_falls_back_to_legacy_when_exec_state_has_empty_pr_urls(tmp_path: Path) -> None:
    (tmp_path / EXEC_STATE_FILE).write_text(json.dumps({"pr_urls": []}))
    (tmp_path / HANDOFF_FILE).write_text(
        json.dumps({"pr_urls": [{"title": "legacy", "url": "https://github.com/o/r/pull/5"}]})
    )
    result = read(tmp_path)
    assert result is not None
    assert result.pr_urls[0].title == "legacy"


def test_read_returns_none_when_both_absent(tmp_path: Path) -> None:
    assert read(tmp_path) is None


def test_read_returns_none_when_both_invalid(tmp_path: Path) -> None:
    (tmp_path / EXEC_STATE_FILE).write_text("bad{")
    (tmp_path / HANDOFF_FILE).write_text("bad{")
    assert read(tmp_path) is None


def test_exec_state_file_constant_value() -> None:
    assert EXEC_STATE_FILE == "exec-state.json"
