"""Tests that orchestrator reads verdict fields from the handoff on every exit.

Task 1 (skeleton): Done-path drain populates outcome_verdict and prep_verdict
from .drain-handoff.json left by the worker.

Task 2 (halt path): a halt entry carries halt_reason plus any verdicts the
worker managed to record in the partial handoff before exiting.

The fake ``claude`` script writes a ``.drain-handoff.json`` with canned
verdicts directly into the worktree directory (its ``$PWD``). In Done-path
tests it also writes to the done-marker so the issue is seen as Done. In
halt-path tests it omits that step, leaving the issue in Todo — which triggers
the not-Done halt.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from drain_cycle import linear, orchestrator, repos


_OUTCOME_VERDICT = {
    "result": "pass",
    "findings": [],
    "invoked_at": "2026-01-01T00:00:00Z",
}
_PREP_VERDICT = {
    "result": "ok",
    "route": "auto-merge",
    "reasoning": "all checks passed",
}
_HANDOFF = {
    "pr_urls": [],  # empty — tests run with no_stack=True, gate is bypassed
    "outcome_verdict": _OUTCOME_VERDICT,
    "prep_verdict": _PREP_VERDICT,
}


def _issue(identifier: str, sort_order: float) -> dict:
    return {
        "id": f"id-{identifier}",
        "identifier": identifier,
        "title": f"Title for {identifier}",
        "description": f"Body for {identifier}",
        "sortOrder": sort_order,
        "state": {"type": "unstarted", "name": "Todo"},
        "labels": ["repo:test-repo"],
    }


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)


def _write_done_script(tmp_path: Path, done_marker: Path) -> Path:
    """Script that marks Done and writes a handoff with verdicts."""
    handoff_json = json.dumps(_HANDOFF)
    script = tmp_path / "fake-claude.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$(basename "$PWD")" >> "{done_marker}"\n'
        # Write the handoff into the current worktree directory.
        f"printf '%s' '{handoff_json}' > .drain-handoff.json\n"
    )
    script.chmod(0o755)
    return script


def _write_halt_script(tmp_path: Path) -> Path:
    """Script that writes a partial handoff with verdicts but does NOT mark Done."""
    handoff_json = json.dumps(_HANDOFF)
    script = tmp_path / "fake-claude.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"printf '%s' '{handoff_json}' > .drain-handoff.json\n"
    )
    script.chmod(0o755)
    return script


def _run_log(tmp_path: Path) -> dict:
    runs_dir = tmp_path / ".drain-cycle" / "runs"
    files = list(runs_dir.glob("stub-cycle-id-*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text())


def test_done_path_entry_carries_verdicts_from_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    only = _issue("ABA-V1", sort_order=1.0)
    issues_by_id = {only["id"]: only}
    done_marker = tmp_path / "done.txt"

    def fake_pending_issues(cycle_id: str):
        completed = {
            line for line in done_marker.read_text().splitlines() if line
        } if done_marker.exists() else set()
        return linear._plan([i for i in [only] if i["identifier"] not in completed])

    def fake_get_issue(issue_id: str) -> dict:
        issue = issues_by_id[issue_id]
        if done_marker.exists() and issue["identifier"] in done_marker.read_text():
            return {**issue, "state": {"type": "completed", "name": "Done"}}
        return issue

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "stub-cycle-id")
    monkeypatch.setattr(linear, "pending_issues", fake_pending_issues)
    monkeypatch.setattr(linear, "get_issue", fake_get_issue)
    monkeypatch.setattr(linear, "set_state", lambda issue_id, state_name: None)
    monkeypatch.setattr(
        orchestrator, "_CLAUDE_CMD", [str(_write_done_script(tmp_path, done_marker))]
    )

    exit_code = orchestrator.run(repos.Repos(mapping={"test-repo": repo}), no_stack=True)
    assert exit_code == 0

    payload = _run_log(tmp_path)
    (entry,) = payload["entries"]
    assert entry["outcome_verdict"] == _OUTCOME_VERDICT
    assert entry["prep_verdict"] == _PREP_VERDICT
    assert entry["halt_reason"] is None


def test_halt_path_entry_carries_halt_reason_and_partial_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    only = _issue("ABA-V2", sort_order=1.0)
    issues_by_id = {only["id"]: only}

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "stub-cycle-id")
    monkeypatch.setattr(
        linear, "pending_issues", lambda cycle_id: linear._plan([only])
    )
    monkeypatch.setattr(linear, "get_issue", lambda issue_id: issues_by_id[issue_id])
    monkeypatch.setattr(linear, "set_state", lambda issue_id, state_name: None)
    monkeypatch.setattr(
        orchestrator, "_CLAUDE_CMD", [str(_write_halt_script(tmp_path))]
    )

    exit_code = orchestrator.run(repos.Repos(mapping={"test-repo": repo}), no_stack=True)
    assert exit_code == 1

    payload = _run_log(tmp_path)
    (entry,) = payload["entries"]
    assert entry["halt_reason"] is not None
    assert entry["final_linear_state"] != "Done"
    # Verdicts from the partial handoff are carried despite the halt.
    assert entry["outcome_verdict"] == _OUTCOME_VERDICT
    assert entry["prep_verdict"] == _PREP_VERDICT
