"""Orchestrator graphite integration tests.

Three behavioural contracts:

1. Stack ordering — two same-repo issues receive parents ``main`` then
   issue-1's branch (= its identifier), so the stack chains in review order.
2. review:high label — applied when the Linear label is present OR findings
   report Critical/Required; never otherwise (four-combination parametrize).
3. gt/gh failure — halts the cycle, records the failure in the run-log with
   the issue's Done state, and leaves the worktree on disk for recovery.

All three use subprocess-mocked ``graphite.*`` so no real ``gt``/``gh`` is
required.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from drain_cycle import graphite, handoff, linear, orchestrator, repos


_TEST_REPO_NAME = "test-repo"


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _issue(
    identifier: str,
    sort_order: float,
    *,
    repo_name: str = _TEST_REPO_NAME,
    labels: list[str] | None = None,
) -> dict:
    if labels is None:
        labels = [f"repo:{repo_name}"]
    return {
        "id": f"id-{identifier}",
        "identifier": identifier,
        "title": f"Title for {identifier}",
        "description": f"Body for {identifier}",
        "sortOrder": sort_order,
        "state": {"type": "unstarted", "name": "Todo"},
        "labels": labels,
    }


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)


def _stub_repos(repo_path: Path) -> repos.Repos:
    return repos.Repos(mapping={_TEST_REPO_NAME: repo_path})


def _completed_identifiers(marker: Path) -> set[str]:
    if not marker.exists():
        return set()
    return {line for line in marker.read_text().splitlines() if line}


def _write_done_claude_script(tmp_path: Path, done_marker: Path) -> Path:
    """Fake ``claude -p`` that appends the worktree basename to done_marker."""
    script = tmp_path / "fake-claude.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$(basename "$PWD")" >> "{done_marker}"\n'
    )
    script.chmod(0o755)
    return script


def _wire_linear_stubs(
    raw_issues: list[dict],
    done_marker: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issues_by_id = {i["id"]: i for i in raw_issues}

    def fake_pending_issues(cycle_id: str):
        completed = _completed_identifiers(done_marker)
        return linear._plan([i for i in raw_issues if i["identifier"] not in completed])

    def fake_get_issue(issue_id: str) -> dict:
        issue = issues_by_id[issue_id]
        if issue["identifier"] in _completed_identifiers(done_marker):
            return {**issue, "state": {"type": "completed", "name": "Done"}}
        return issue

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "stub-cycle")
    monkeypatch.setattr(linear, "pending_issues", fake_pending_issues)
    monkeypatch.setattr(linear, "get_issue", fake_get_issue)
    monkeypatch.setattr(linear, "set_state", lambda issue_id, state_name: None)


# --------------------------------------------------------------------------- #
# Test 1: Stack parent chain
# --------------------------------------------------------------------------- #

def test_graphite_submit_receives_main_then_first_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For two same-repo issues, submit receives parent 'main' for the first
    issue and the first issue's identifier (its branch name) for the second."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    first = _issue("ABA-FIRST", sort_order=1.0)
    second = _issue("ABA-SECOND", sort_order=2.0)
    done_marker = tmp_path / "done.txt"

    _wire_linear_stubs([first, second], done_marker, monkeypatch)
    monkeypatch.setattr(
        orchestrator, "_CLAUDE_CMD", [str(_write_done_claude_script(tmp_path, done_marker))]
    )
    monkeypatch.setattr(handoff, "read", lambda path: None)

    submit_calls: list[dict] = []
    call_counter = {"n": 0}

    def fake_submit(
        worktree: Path, *, parent: str, title: str = "", body: str = ""
    ) -> graphite.PrInfo:
        call_counter["n"] += 1
        submit_calls.append({"parent": parent})
        return graphite.PrInfo(url=f"https://github.com/r/p/pull/{call_counter['n']}", number=call_counter["n"])

    monkeypatch.setattr(graphite, "submit", fake_submit)
    monkeypatch.setattr(graphite, "ensure_review_high_label", lambda worktree: None)
    monkeypatch.setattr(graphite, "add_label", lambda pr_number, label, worktree: None)

    exit_code = orchestrator.run(_stub_repos(repo), stack=True)

    assert exit_code == 0
    assert len(submit_calls) == 2
    assert submit_calls[0]["parent"] == "main"
    assert submit_calls[1]["parent"] == first["identifier"]


# --------------------------------------------------------------------------- #
# Test 2: review:high label logic
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "has_label,critical,required,expect_high",
    [
        (True, 0, 0, True),   # Linear label present → high regardless of findings
        (False, 1, 0, True),  # Critical finding → high
        (False, 0, 1, True),  # Required finding → high
        (False, 0, 0, False), # No label, no findings → not high
    ],
    ids=["label-only", "critical-finding", "required-finding", "no-flag"],
)
def test_review_high_label_applied_correctly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    has_label: bool,
    critical: int,
    required: int,
    expect_high: bool,
) -> None:
    """review:high is applied when the Linear label is present OR findings
    report Critical/Required, and never otherwise."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    labels = [f"repo:{_TEST_REPO_NAME}"]
    if has_label:
        labels.append("review:high")
    issue = _issue("ABA-ONLY", sort_order=1.0, labels=labels)
    done_marker = tmp_path / "done.txt"

    _wire_linear_stubs([issue], done_marker, monkeypatch)
    monkeypatch.setattr(
        orchestrator, "_CLAUDE_CMD", [str(_write_done_claude_script(tmp_path, done_marker))]
    )
    monkeypatch.setattr(
        handoff, "read",
        lambda path: handoff.HandoffData(
            pr_title="Test PR",
            pr_body="## What\nbody",
            findings={"critical": critical, "required": required},
        ),
    )

    add_label_calls: list[tuple] = []
    ensure_calls: list[bool] = []

    def fake_submit(
        worktree: Path, *, parent: str, title: str = "", body: str = ""
    ) -> graphite.PrInfo:
        return graphite.PrInfo(url="https://github.com/r/p/pull/1", number=1)

    def fake_ensure(worktree: Path) -> None:
        ensure_calls.append(True)

    def fake_add(pr_number: int, label: str, worktree: Path) -> None:
        add_label_calls.append((pr_number, label))

    monkeypatch.setattr(graphite, "submit", fake_submit)
    monkeypatch.setattr(graphite, "ensure_review_high_label", fake_ensure)
    monkeypatch.setattr(graphite, "add_label", fake_add)

    exit_code = orchestrator.run(_stub_repos(repo), stack=True)

    assert exit_code == 0
    if expect_high:
        assert bool(ensure_calls), "ensure_review_high_label should have been called"
        assert any(
            label == "review:high" for _, label in add_label_calls
        ), "review:high should have been added to the PR"
    else:
        assert not ensure_calls, "ensure_review_high_label should not have been called"
        assert not add_label_calls, "add_label should not have been called"


# --------------------------------------------------------------------------- #
# Test 3: gt submit failure → halt + run-log + worktree preserved
# --------------------------------------------------------------------------- #

def test_graphite_failure_halts_cycle_and_preserves_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A simulated gt submit failure must halt the cycle, record the failure
    in the run-log (with the issue's Done final state), and leave the worktree
    on disk for recovery. The second issue must never be attempted."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    first = _issue("ABA-FIRST", sort_order=1.0)
    second = _issue("ABA-SECOND", sort_order=2.0)
    done_marker = tmp_path / "done.txt"

    _wire_linear_stubs([first, second], done_marker, monkeypatch)
    monkeypatch.setattr(
        orchestrator, "_CLAUDE_CMD", [str(_write_done_claude_script(tmp_path, done_marker))]
    )
    monkeypatch.setattr(handoff, "read", lambda path: None)

    graphite_error = "gt submit failed: auth token missing"

    def failing_submit(
        worktree: Path, *, parent: str, title: str = "", body: str = ""
    ) -> graphite.PrInfo:
        raise RuntimeError(graphite_error)

    monkeypatch.setattr(graphite, "submit", failing_submit)

    exit_code = orchestrator.run(_stub_repos(repo), stack=True)

    assert exit_code == 1

    # Worktree for the first issue preserved (not removed on graphite halt).
    first_worktree = repo / ".worktrees" / first["identifier"]
    assert first_worktree.is_dir()

    # Second issue never attempted (cycle halted after first).
    second_worktree = repo / ".worktrees" / second["identifier"]
    assert not second_worktree.exists()

    # Run-log records the failure.
    runs_dir = tmp_path / ".drain-cycle" / "runs"
    log_files = list(runs_dir.glob("stub-cycle-*.json"))
    assert len(log_files) == 1
    payload = json.loads(log_files[0].read_text())
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["issue_identifier"] == first["identifier"]
    # Issue reached Done in Linear; the halt is at the orchestrator/stack layer.
    assert entry["final_linear_state"] == "Done"
    assert entry["halt_reason"] is not None
    assert graphite_error in entry["halt_reason"]

    # Halt line on stderr carries the error detail.
    stderr_lines = capsys.readouterr().err.splitlines()
    halt_lines = [line for line in stderr_lines if line.startswith("Halt: ")]
    assert len(halt_lines) == 1
    assert graphite_error in halt_lines[0]
