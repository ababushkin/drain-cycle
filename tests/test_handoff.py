"""Tests for ``drain_cycle.handoff``.

``read`` must never raise: a missing file, a truncated JSON write, or a
structurally wrong payload all return ``None``. A well-formed file returns
a typed ``HandoffData`` object listing the submitted PRs. An empty
``finish.pr_urls`` list reads as ``None`` — the skill writes URLs only after a
PR is actually created, so "present but empty" means submission never happened.

The only state file is the sectioned ``exec-state.json`` (ADR 0030). The legacy
flat ``.drain-handoff.json`` has been dropped and is never read.
"""
from __future__ import annotations

import json
from pathlib import Path

from drain_cycle.handoff import EXEC_STATE_FILE, HandoffData, PullRequest, read, read_partial


def _valid_data() -> HandoffData:
    return HandoffData(
        pr_urls=(
            PullRequest(title="feat: crash-proof filter", url="https://github.com/o/r/pull/1"),
            PullRequest(title="feat: transcript core", url="https://github.com/o/r/pull/2"),
        )
    )


def _write_exec_state(tmp_path: Path, payload: object) -> None:
    (tmp_path / EXEC_STATE_FILE).write_text(json.dumps(payload))


def _finish(pr_urls: object) -> dict:
    """Wrap ``pr_urls`` in the ``finish`` section the supervisor reads."""
    return {"finish": {"pr_urls": pr_urls}}


# --- finish.pr_urls validation ---


def test_read_valid_exec_state_returns_data(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, _finish([
        {"title": "feat: crash-proof filter", "url": "https://github.com/o/r/pull/1"},
        {"title": "feat: transcript core", "url": "https://github.com/o/r/pull/2"},
    ]))
    result = read(tmp_path)
    assert result is not None
    assert result.pr_urls == _valid_data().pr_urls


def test_read_missing_file_returns_none(tmp_path: Path) -> None:
    assert read(tmp_path) is None


def test_read_malformed_json_returns_none(tmp_path: Path) -> None:
    (tmp_path / EXEC_STATE_FILE).write_text("not valid json{{{")
    assert read(tmp_path) is None


def test_read_wrong_top_level_type_returns_none(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, [1, 2, 3])
    assert read(tmp_path) is None


def test_read_missing_finish_section_returns_none(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, {"other": "key"})
    assert read(tmp_path) is None


def test_read_empty_pr_urls_returns_none(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, _finish([]))
    assert read(tmp_path) is None


def test_read_pr_urls_not_list_returns_none(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, _finish({"title": "x", "url": "y"}))
    assert read(tmp_path) is None


def test_read_entry_not_dict_returns_none(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, _finish(["just a string"]))
    assert read(tmp_path) is None


def test_read_entry_missing_url_returns_none(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, _finish([{"title": "no url here"}]))
    assert read(tmp_path) is None


def test_read_entry_empty_url_returns_none(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, _finish([{"title": "t", "url": ""}]))
    assert read(tmp_path) is None


def test_read_entry_title_not_string_returns_none(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, _finish([{"title": 42, "url": "https://x/pull/1"}]))
    assert read(tmp_path) is None


def test_read_entry_url_not_string_returns_none(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, _finish([{"title": "t", "url": 42}]))
    assert read(tmp_path) is None


def test_read_entry_missing_title_returns_none(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, _finish([{"url": "https://x/pull/1"}]))
    assert read(tmp_path) is None


def test_read_entry_empty_title_is_accepted(tmp_path: Path) -> None:
    # title is informational; url is the canonical identity. An empty title is
    # accepted on purpose (unlike an empty url, which invalidates the entry).
    _write_exec_state(tmp_path, _finish([{"title": "", "url": "https://x/pull/1"}]))
    result = read(tmp_path)
    assert result is not None
    assert result.pr_urls == (PullRequest(title="", url="https://x/pull/1"),)


def test_read_mixed_valid_and_invalid_entries_returns_none(tmp_path: Path) -> None:
    # One good PR followed by a malformed one (e.g. a partial write): the list
    # is all-or-nothing, so a single bad entry invalidates the whole handoff.
    _write_exec_state(tmp_path, _finish([
        {"title": "good", "url": "https://x/pull/1"},
        {"title": "bad", "url": ""},
    ]))
    assert read(tmp_path) is None


def test_read_never_raises_on_empty_file(tmp_path: Path) -> None:
    (tmp_path / EXEC_STATE_FILE).write_text("")
    assert read(tmp_path) is None


# --- legacy file dropped ---


def test_legacy_handoff_file_is_not_read(tmp_path: Path) -> None:
    # Even if .drain-handoff.json exists with valid pr_urls, read() must ignore
    # it: exec-state.json is the only state file.
    legacy_payload = {
        "pr_urls": [{"title": "from legacy", "url": "https://github.com/o/r/pull/20"}]
    }
    (tmp_path / ".drain-handoff.json").write_text(json.dumps(legacy_payload))
    assert read(tmp_path) is None
    assert read_partial(tmp_path) == (None, None)


# --- verify section → outcome_verdict ---


def test_read_maps_verify_pass_to_outcome_verdict(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, {
        "finish": {"pr_urls": [{"title": "t", "url": "https://x/pull/1"}]},
        "verify": {
            "verdict": "PASS",
            "ac_results": [{"item": "does X", "result": "PASS"}],
        },
    })
    result = read(tmp_path)
    assert result is not None
    assert result.outcome_verdict == {"result": "pass", "findings": []}
    assert result.prep_verdict is None


def test_read_maps_verify_fail_with_failed_ac_findings(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, {
        "finish": {"pr_urls": [{"title": "t", "url": "https://x/pull/1"}]},
        "verify": {
            "verdict": "FAIL",
            "ac_results": [
                {"item": "does X", "result": "PASS"},
                {"item": "does Y", "result": "FAIL"},
            ],
        },
    })
    result = read(tmp_path)
    assert result is not None
    assert result.outcome_verdict == {"result": "fail", "findings": ["does Y"]}


def test_read_no_verify_section_leaves_outcome_none(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, _finish([{"title": "t", "url": "https://x/pull/1"}]))
    result = read(tmp_path)
    assert result is not None
    assert result.outcome_verdict is None


def test_read_prep_verdict_always_none(tmp_path: Path) -> None:
    # The exec:* workflow has no prep-verdict producer, so the sectioned reader
    # never surfaces one even when every other section is present.
    _write_exec_state(tmp_path, _PACK_FIXTURE_PAYLOAD())
    result = read(tmp_path)
    assert result is not None
    assert result.prep_verdict is None


def test_verify_to_outcome_tolerates_malformed_sections(tmp_path: Path) -> None:
    # The verify-section mapping is a crash-proofing guard: a non-dict section,
    # non-dict ac_results entries, an unrecognised verdict, or a non-string item
    # must never raise — they degrade to None or a skipped finding.
    cases = [
        {"verify": "not-a-dict"},
        {"verify": {"verdict": "MAYBE"}},  # unrecognised verdict → None
        {"verify": {"verdict": "PASS", "ac_results": "not-a-list"}},
        {"verify": {"verdict": "FAIL", "ac_results": ["str", 7, None]}},
        {"verify": {"verdict": "FAIL", "ac_results": [{"item": 42, "result": "FAIL"}]}},
    ]
    for sections in cases:
        payload = {"finish": {"pr_urls": [{"title": "t", "url": "https://x/pull/1"}]}, **sections}
        _write_exec_state(tmp_path, payload)
        result = read(tmp_path)
        assert result is not None  # finish.pr_urls is still valid
        # No crash; verdict is either None (unrecognised) or has no spurious findings.
        if result.outcome_verdict is not None:
            assert result.outcome_verdict["findings"] == []


# --- read_partial ---


def test_read_partial_reads_verify_section_from_exec_state(tmp_path: Path) -> None:
    # A halt after verify but before finish: no finish.pr_urls (read() → None),
    # yet read_partial must still surface the verify verdict from the section.
    _write_exec_state(tmp_path, {
        "verify": {
            "verdict": "FAIL",
            "ac_results": [{"item": "does Y", "result": "FAIL"}],
        }
    })
    assert read(tmp_path) is None
    got_ov, got_pv = read_partial(tmp_path)
    assert got_ov == {"result": "fail", "findings": ["does Y"]}
    assert got_pv is None


def test_read_partial_no_verify_section_returns_none_none(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, _finish([{"title": "t", "url": "https://x/pull/1"}]))
    assert read_partial(tmp_path) == (None, None)


def test_read_partial_returns_none_none_on_missing_file(tmp_path: Path) -> None:
    assert read_partial(tmp_path) == (None, None)


def test_read_partial_returns_none_none_on_malformed_json(tmp_path: Path) -> None:
    (tmp_path / EXEC_STATE_FILE).write_text("not json{{{")
    assert read_partial(tmp_path) == (None, None)


def test_read_partial_returns_none_none_on_non_dict_payload(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, [1, 2, 3])
    assert read_partial(tmp_path) == (None, None)


def test_exec_state_file_constant_value() -> None:
    assert EXEC_STATE_FILE == "exec-state.json"


# --- cross-repo fixture parity ---
#
# tests/fixtures/exec-state.json is a manually vendored copy of the pack's
# authoritative sample (agent-skills-shaper/fixtures/exec-state/exec-state.json
# — what pr-finishing actually writes). Running it through the supervisor's
# reader is the test that would have caught the top-level-vs-finish.pr_urls
# divergence. There is no automated cross-repo diff: re-vendor this file by hand
# whenever the pack's exec-state schema changes.


_PACK_FIXTURE = Path(__file__).parent / "fixtures" / "exec-state.json"


def _PACK_FIXTURE_PAYLOAD() -> dict:
    return json.loads(_PACK_FIXTURE.read_text())


def test_pack_fixture_reads_through_supervisor(tmp_path: Path) -> None:
    (tmp_path / EXEC_STATE_FILE).write_text(_PACK_FIXTURE.read_text())
    result = read(tmp_path)
    assert result is not None
    # finish.pr_urls is the submission signal.
    assert result.pr_urls == (
        PullRequest(
            title="feat: update pr-finishing to dual-write exec-state.json",
            url="https://github.com/example/repo/pull/1",
        ),
    )
    # verify: PASS → outcome_verdict result "pass", no failing AC items.
    assert result.outcome_verdict == {"result": "pass", "findings": []}


def test_pack_fixture_read_partial_surfaces_verify_verdict(tmp_path: Path) -> None:
    (tmp_path / EXEC_STATE_FILE).write_text(_PACK_FIXTURE.read_text())
    got_ov, got_pv = read_partial(tmp_path)
    assert got_ov == {"result": "pass", "findings": []}
    assert got_pv is None


# --- review section → review_verdict ---


def test_read_maps_review_go_to_review_verdict(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, {
        "finish": {"pr_urls": [{"title": "t", "url": "https://x/pull/1"}]},
        "review": {"verdict": "GO", "findings": []},
    })
    result = read(tmp_path)
    assert result is not None
    assert result.review_verdict == {"result": "go", "findings": []}


def test_read_maps_review_no_go_to_review_verdict(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, {
        "finish": {"pr_urls": [{"title": "t", "url": "https://x/pull/1"}]},
        "review": {"verdict": "NO-GO", "findings": ["missing test", "style issue"]},
    })
    result = read(tmp_path)
    assert result is not None
    assert result.review_verdict == {"result": "no-go", "findings": ["missing test", "style issue"]}


def test_read_no_review_section_leaves_review_verdict_none(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, _finish([{"title": "t", "url": "https://x/pull/1"}]))
    result = read(tmp_path)
    assert result is not None
    assert result.review_verdict is None


def test_review_to_verdict_drops_unrecognised_verdict(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, {
        "finish": {"pr_urls": [{"title": "t", "url": "https://x/pull/1"}]},
        "review": {"verdict": "MAYBE", "findings": []},
    })
    result = read(tmp_path)
    assert result is not None
    assert result.review_verdict is None


def test_review_to_verdict_tolerates_missing_findings(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, {
        "finish": {"pr_urls": [{"title": "t", "url": "https://x/pull/1"}]},
        "review": {"verdict": "GO"},
    })
    result = read(tmp_path)
    assert result is not None
    assert result.review_verdict == {"result": "go", "findings": []}


def test_review_to_verdict_tolerates_non_dict_section(tmp_path: Path) -> None:
    _write_exec_state(tmp_path, {
        "finish": {"pr_urls": [{"title": "t", "url": "https://x/pull/1"}]},
        "review": "not-a-dict",
    })
    result = read(tmp_path)
    assert result is not None
    assert result.review_verdict is None


def test_pack_fixture_carries_review_verdict(tmp_path: Path) -> None:
    (tmp_path / EXEC_STATE_FILE).write_text(_PACK_FIXTURE.read_text())
    result = read(tmp_path)
    assert result is not None
    assert result.review_verdict == {"result": "go", "findings": []}
