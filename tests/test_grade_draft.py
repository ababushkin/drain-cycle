"""Tests for ``drain-cycle grade-draft``.

Covers: happy-path write, overwrite idempotence, missing-runs-dir error,
missing-entry error, most-recent-entry selection, and CLI dispatch.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drain_cycle import cli, grade_draft


def _entry(
    identifier: str,
    state: str = "Done",
    *,
    duration: float = 120.0,
    exit_code: int = 0,
    model: str = "claude-sonnet-4-6",
    num_turns: int = 8,
    cost_usd: float = 0.12,
    cumulative: int = 40000,
    halt_reason: str | None = None,
) -> dict:
    return {
        "issue_identifier": identifier,
        "started_at": "2026-06-01T10:00:00+00:00",
        "finished_at": "2026-06-01T10:02:00+00:00",
        "exit_code": exit_code,
        "final_linear_state": state,
        "worktree_path": f"/tmp/repo/.worktrees/{identifier}",
        "halt_reason": halt_reason,
        "duration_seconds": duration,
        "model": model,
        "usage": {
            "input_tokens": 30000,
            "output_tokens": 10000,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cumulative": cumulative,
            "peak_context": 5000,
        },
        "cost_usd": cost_usd,
        "num_turns": num_turns,
        "session_id": "stub-session-id",
        "is_error": False,
    }


def _write_run_log(
    runs_dir: Path, cycle_id: str, timestamp: str, entries: list[dict]
) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{cycle_id}-{timestamp}.json"
    path.write_text(
        json.dumps(
            {
                "cycle_id": cycle_id,
                "cycle_duration_seconds": 0.0,
                "cycle_cost_usd": 0.0,
                "cycle_tokens_cumulative": 0,
                "cycle_halt_reason": None,
                "entries": entries,
            }
        )
        + "\n"
    )
    return path


# ---------------------------------------------------------------------------
# write_draft_from_entry
# ---------------------------------------------------------------------------


def test_write_draft_creates_grade_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    path = grade_draft.write_draft_from_entry("ABA-1", _entry("ABA-1"))

    assert path.exists()
    content = path.read_text()
    assert "issue: ABA-1" in content
    assert "status: draft" in content


def test_write_draft_populates_kr_checklist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    entry = _entry("ABA-2", state="Done", duration=95.0)

    path = grade_draft.write_draft_from_entry("ABA-2", entry)
    content = path.read_text()

    assert "KR1" in content
    assert "Done" in content
    assert "KR2" in content
    assert "95.0s" in content


def test_write_draft_overwrites_on_second_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    grade_draft.write_draft_from_entry("ABA-3", _entry("ABA-3", state="Done"))
    grade_draft.write_draft_from_entry("ABA-3", _entry("ABA-3", state="In Progress"))

    content = (grade_draft.grades_dir() / "ABA-3.md").read_text()
    # Only the second write's state is present; no duplication.
    assert content.count("status: draft") == 1
    assert "In Progress" in content
    assert content.count("## KR check") == 1


def test_write_draft_creates_grades_dir_if_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert not grade_draft.grades_dir().exists()

    grade_draft.write_draft_from_entry("ABA-4", _entry("ABA-4"))

    assert grade_draft.grades_dir().is_dir()


def test_write_draft_renders_null_fields_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entries written before a worker session (pre-spawn halts) have null
    for model/usage/cost_usd/num_turns. The rendered draft must not crash."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sparse_entry = {
        "issue_identifier": "ABA-5",
        "started_at": "2026-06-01T10:00:00+00:00",
        "finished_at": "2026-06-01T10:00:01+00:00",
        "exit_code": -1,
        "final_linear_state": "Todo",
        "worktree_path": "<unresolved>",
        "halt_reason": "Halt: ABA-5 (Todo) — repo not found",
        "duration_seconds": 1.0,
        "model": None,
        "usage": None,
        "cost_usd": None,
        "num_turns": None,
        "session_id": None,
        "is_error": None,
    }

    path = grade_draft.write_draft_from_entry("ABA-5", sparse_entry)
    content = path.read_text()

    assert "issue: ABA-5" in content
    assert "status: draft" in content
    assert "—" in content  # at least one null field rendered as dash


# ---------------------------------------------------------------------------
# run() (CLI entry point)
# ---------------------------------------------------------------------------


def test_run_exits_one_when_runs_dir_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    code = grade_draft.run("ABA-10")

    assert code != 0
    assert "no run logs" in capsys.readouterr().err.lower()


def test_run_exits_one_when_no_entry_for_issue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runs = tmp_path / ".drain-cycle" / "runs"
    _write_run_log(runs, "cycle-1", "20260601T100000000000Z", [_entry("ABA-11")])

    code = grade_draft.run("ABA-99")

    assert code != 0
    err = capsys.readouterr().err
    assert "ABA-99" in err
    assert "no run-log entry" in err.lower()


def test_run_writes_draft_and_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    runs = tmp_path / ".drain-cycle" / "runs"
    _write_run_log(runs, "cycle-1", "20260601T100000000000Z", [_entry("ABA-20")])

    code = grade_draft.run("ABA-20")

    assert code == 0
    dest = grade_draft.grade_path("ABA-20")
    assert dest.exists()
    assert "issue: ABA-20" in dest.read_text()


def test_run_picks_most_recent_file_for_issue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the same issue appears in two run files, the entry from the
    lexicographically later (more recent) file is used."""
    monkeypatch.setenv("HOME", str(tmp_path))
    runs = tmp_path / ".drain-cycle" / "runs"
    _write_run_log(
        runs,
        "cycle-1",
        "20260601T090000000000Z",
        [_entry("ABA-30", state="In Progress")],
    )
    _write_run_log(
        runs,
        "cycle-1",
        "20260601T100000000000Z",
        [_entry("ABA-30", state="Done")],
    )

    grade_draft.run("ABA-30")

    content = grade_draft.grade_path("ABA-30").read_text()
    # Most-recent file has state=Done.
    assert "Done" in content
    assert "In Progress" not in content


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------


def test_cli_grade_draft_dispatches_correctly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called: list[str] = []

    def fake_run(issue_identifier: str) -> int:
        called.append(issue_identifier)
        return 0

    monkeypatch.setattr(grade_draft, "run", fake_run)
    monkeypatch.setattr(cli, "load_dotenv", lambda *_a, **_kw: False)
    monkeypatch.setattr("sys.argv", ["drain-cycle", "grade-draft", "ABA-42"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 0
    assert called == ["ABA-42"]


def test_cli_grade_draft_without_identifier_exits_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``drain-cycle grade-draft`` with no issue identifier is an unknown invocation."""
    monkeypatch.setattr(cli, "load_dotenv", lambda *_a, **_kw: False)
    monkeypatch.setattr("sys.argv", ["drain-cycle", "grade-draft"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    assert "unknown invocation" in capsys.readouterr().err
