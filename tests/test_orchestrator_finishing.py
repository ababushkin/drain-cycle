"""Tests for orchestrator-enforced completion via finishing sub-agent.

The orchestrator spawns a sonnet finishing sub-agent when a worker exits
without completing the protocol but has committed work beyond the base branch.
Two recovery sites are covered:
  * not-Done halt: worker exits without marking the issue Done
  * stack-no-PRs halt: worker marks Done but leaves no pr_urls in the handoff

Both use the same sub-agent (``_FINISHING_MODEL``), a one-attempt-per-issue-per-run
guard, and write a ``finishing_runs`` entry in the run log.

Substitution pattern mirrors ``test_orchestrator_halt.py``: real git repo,
in-process Linear stubs, fake ``claude`` shell script as ``_CLAUDE_CMD``.

Scripts use an invocation counter file (``<tmp_path>/invocation_count.txt``)
to distinguish the main worker (first invocation) from the finishing sub-agent
(second invocation). First invocation makes a commit in the worktree so
``_commits_beyond_base`` returns True; second invocation runs the finishing
protocol (marks Done, writes handoff when in stack mode).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from drain_cycle import linear, orchestrator, repos


_TEST_REPO_NAME = "test-repo"


def _issue(
    identifier: str,
    sort_order: float = 1.0,
    *,
    repo_name: str = _TEST_REPO_NAME,
) -> dict:
    return {
        "id": f"id-{identifier}",
        "identifier": identifier,
        "title": f"Title for {identifier}",
        "description": f"Body for {identifier}",
        "sortOrder": sort_order,
        "state": {"type": "unstarted", "name": "Todo"},
        "labels": [f"repo:{repo_name}"],
    }


def _stub_repos(repo_path: Path) -> repos.Repos:
    return repos.Repos(mapping={_TEST_REPO_NAME: repo_path})


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)


def _write_two_invocation_script(
    tmp_path: Path,
    done_marker: Path,
    *,
    second_writes_handoff: bool = False,
) -> Path:
    """Script that behaves differently on first vs second invocation.

    First invocation (main worker): makes a git commit in the worktree to
    simulate committed work, then exits without marking Done.
    Second invocation (finishing sub-agent): marks the issue Done by writing
    to ``done_marker``, and optionally writes ``pr_urls`` to the handoff file.
    """
    counter_file = tmp_path / "invocation_count.txt"
    handoff_clause = ""
    if second_writes_handoff:
        handoff_clause = (
            'printf \'{"pr_urls": [{"title": "PR #1", "url": "https://github.com/r/p/1"}]}\' '
            "> .drain-handoff.json\n"
        )
    script = tmp_path / "fake-claude.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'count=$(cat "{counter_file}" 2>/dev/null || echo 0)\n'
        'count=$((count + 1))\n'
        f'printf "%s" "$count" > "{counter_file}"\n'
        'if [ "$count" -ge 2 ]; then\n'
        # Second invocation: mark Done + optionally write handoff
        f'  printf "%s\\n" "$(basename "$PWD")" >> "{done_marker}"\n'
        f"  {handoff_clause}"
        "  exit 0\n"
        "else\n"
        # First invocation: make a commit to create work beyond base
        '  git config user.email "test@test.com" 2>/dev/null\n'
        '  git config user.name "Test" 2>/dev/null\n'
        '  touch work.txt\n'
        "  git add work.txt 2>/dev/null\n"
        '  git commit -m "work" 2>/dev/null\n'
        "  exit 0\n"
        "fi\n"
    )
    script.chmod(0o755)
    return script


def _write_no_commit_script(tmp_path: Path) -> Path:
    """Script that exits without making commits or marking Done."""
    script = tmp_path / "fake-claude.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    return script


def _write_commit_but_stay_not_done_script(tmp_path: Path) -> Path:
    """Script that makes a commit on every invocation but never marks Done.

    Used to test the "finishing sub-agent ran but still not Done" path:
    both the main worker and the finishing sub-agent make commits and exit.
    """
    script = tmp_path / "fake-claude.sh"
    script.write_text(
        "#!/bin/sh\n"
        'git config user.email "test@test.com" 2>/dev/null\n'
        'git config user.name "Test" 2>/dev/null\n'
        'printf "%s" "$(date +%s%N)" > work.txt\n'
        "git add work.txt 2>/dev/null\n"
        'git commit -m "work" 2>/dev/null\n'
        "exit 0\n"
    )
    script.chmod(0o755)
    return script


def _write_commit_with_fail_verdict_script(tmp_path: Path) -> Path:
    """Commit + write FAIL outcome_verdict to handoff on every invocation.

    Used to verify that a verifier FAIL blocks finishing recovery: the
    orchestrator must read the FAIL verdict from the handoff and skip
    the finishing sub-agent regardless of commits present.
    """
    counter_file = tmp_path / "invocation_count.txt"
    handoff = json.dumps({
        "pr_urls": [],
        "outcome_verdict": {
            "result": "fail",
            "findings": ["test coverage missing"],
            "invoked_at": "2026-01-01T00:00:00Z",
        },
    })
    script = tmp_path / "fake-claude.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'count=$(cat "{counter_file}" 2>/dev/null || echo 0)\n'
        'count=$((count + 1))\n'
        f'printf "%s" "$count" > "{counter_file}"\n'
        'git config user.email "test@test.com" 2>/dev/null\n'
        'git config user.name "Test" 2>/dev/null\n'
        'touch work.txt\n'
        "git add work.txt 2>/dev/null\n"
        'git commit -m "work" 2>/dev/null\n'
        f"cat > .drain-handoff.json << 'EOFHANDOFF'\n{handoff}\nEOFHANDOFF\n"
        "exit 0\n"
    )
    script.chmod(0o755)
    return script


def _write_done_no_prs_then_finishing_script(
    tmp_path: Path,
    done_marker: Path,
) -> Path:
    """First invocation: mark Done but write no pr_urls.
    Second invocation (finishing sub-agent): write pr_urls handoff.

    Used to test stack-no-PRs recovery.
    """
    counter_file = tmp_path / "invocation_count.txt"
    script = tmp_path / "fake-claude.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'count=$(cat "{counter_file}" 2>/dev/null || echo 0)\n'
        'count=$((count + 1))\n'
        f'printf "%s" "$count" > "{counter_file}"\n'
        'if [ "$count" -ge 2 ]; then\n'
        # Second invocation: write pr_urls (issue already Done)
        '  printf \'{"pr_urls": [{"title": "PR #1", "url": "https://github.com/r/p/1"}]}\''
        " > .drain-handoff.json\n"
        "  exit 0\n"
        "else\n"
        # First invocation: make a commit, mark Done, but omit pr_urls
        '  git config user.email "test@test.com" 2>/dev/null\n'
        '  git config user.name "Test" 2>/dev/null\n'
        '  touch work.txt\n'
        "  git add work.txt 2>/dev/null\n"
        '  git commit -m "work" 2>/dev/null\n'
        f'  printf "%s\\n" "$(basename "$PWD")" >> "{done_marker}"\n'
        "  exit 0\n"
        "fi\n"
    )
    script.chmod(0o755)
    return script


def _read_run_log(tmp_path: Path, cycle_id: str) -> dict:
    runs_dir = tmp_path / ".drain-cycle" / "runs"
    files = list(runs_dir.glob(f"{cycle_id}-*.json"))
    assert len(files) == 1, f"expected one run-log file, found {len(files)}"
    return json.loads(files[0].read_text())


# ---------------------------------------------------------------------------
# not-Done recovery: finishing sub-agent succeeds
# ---------------------------------------------------------------------------


def test_finishing_sub_agent_recovers_not_done_with_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not-Done + commits → finishing sub-agent runs and marks Done → run continues."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    issue = _issue("ABA-ONE")
    issues_by_id = {issue["id"]: issue}
    done_marker = tmp_path / "done-identifiers.txt"

    def fake_get_issue(issue_id: str) -> dict:
        base = issues_by_id[issue_id]
        if base["identifier"] in _completed_identifiers(done_marker):
            return {**base, "state": {"type": "completed", "name": "Done"}}
        return base

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "stub-cycle")
    monkeypatch.setattr(linear, "pending_issues", lambda c: linear._plan([issue]))
    monkeypatch.setattr(linear, "get_issue", fake_get_issue)
    monkeypatch.setattr(linear, "set_state", lambda iid, s: None)

    script = _write_two_invocation_script(tmp_path, done_marker)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(script)])

    exit_code = orchestrator.run(_stub_repos(repo), no_stack=True)

    assert exit_code == 0
    payload = _read_run_log(tmp_path, "stub-cycle")
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["final_linear_state"] == "Done"
    assert entry["halt_reason"] is None
    # finishing_runs records the sub-agent spawn
    assert len(entry["finishing_runs"]) == 1
    assert entry["finishing_runs"][0]["trigger"] == "err-issue-not-done"


def test_finishing_sub_agent_not_done_recovery_run_continues_to_next_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After not-Done recovery, the run continues to the next issue."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    first = _issue("ABA-FIRST", sort_order=1.0)
    second = _issue("ABA-SECOND", sort_order=2.0)
    raw_issues = [first, second]
    issues_by_id = {i["id"]: i for i in raw_issues}
    done_marker = tmp_path / "done-identifiers.txt"
    # Separate counter file for the second issue so it doesn't share state
    # with the first issue's two-invocation script.
    counter_file = tmp_path / "invocation_count.txt"

    def fake_pending_issues(cycle_id: str) -> list:
        completed = _completed_identifiers(done_marker)
        return linear._plan([i for i in raw_issues if i["identifier"] not in completed])

    def fake_get_issue(issue_id: str) -> dict:
        base = issues_by_id[issue_id]
        if base["identifier"] in _completed_identifiers(done_marker):
            return {**base, "state": {"type": "completed", "name": "Done"}}
        return base

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "stub-cycle")
    monkeypatch.setattr(linear, "pending_issues", fake_pending_issues)
    monkeypatch.setattr(linear, "get_issue", fake_get_issue)
    monkeypatch.setattr(linear, "set_state", lambda iid, s: None)

    # Script: two-invocation logic for first issue; for second issue, counter
    # is already at 2+ so second invocation marks Done directly.
    # But the counter is shared across issues. For first issue:
    #   invocation 1 → count=1 → commit, no Done
    #   invocation 2 (finishing) → count=2 → Done
    # For second issue:
    #   invocation 3 → count=3 >= 2 → Done immediately (correct)
    script = _write_two_invocation_script(tmp_path, done_marker)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(script)])

    exit_code = orchestrator.run(_stub_repos(repo), no_stack=True)

    assert exit_code == 0
    assert "ABA-FIRST" in _completed_identifiers(done_marker)
    assert "ABA-SECOND" in _completed_identifiers(done_marker)


# ---------------------------------------------------------------------------
# not-Done recovery: finishing sub-agent fails to mark Done
# ---------------------------------------------------------------------------


def test_finishing_sub_agent_not_done_halts_with_recovery_attempted_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not-Done + commits + finishing still not Done → halt names the attempt."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    issue = _issue("ABA-FAIL")
    issues_by_id = {issue["id"]: issue}

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "stub-cycle")
    monkeypatch.setattr(linear, "pending_issues", lambda c: linear._plan([issue]))
    monkeypatch.setattr(linear, "get_issue", lambda iid: issues_by_id[iid])
    monkeypatch.setattr(linear, "set_state", lambda iid, s: None)

    # Script commits work on every invocation but never marks Done
    script = _write_commit_but_stay_not_done_script(tmp_path)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(script)])

    exit_code = orchestrator.run(_stub_repos(repo), no_stack=True)

    assert exit_code != 0
    stderr = capsys.readouterr().err
    halt_lines = [l for l in stderr.splitlines() if l.startswith("Halt: ")]
    assert len(halt_lines) == 1
    assert "finishing sub-agent attempted" in halt_lines[0]

    payload = _read_run_log(tmp_path, "stub-cycle")
    entry = payload["entries"][0]
    assert entry["final_linear_state"] != "Done"
    assert "finishing sub-agent attempted" in entry["halt_reason"]
    assert len(entry["finishing_runs"]) == 1
    assert entry["finishing_runs"][0]["trigger"] == "err-issue-not-done"


# ---------------------------------------------------------------------------
# not-Done: no recovery when branch has no commits beyond base
# ---------------------------------------------------------------------------


def test_no_finishing_when_no_commits_beyond_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not-Done with no commits → no finishing sub-agent, halt normally."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    issue = _issue("ABA-EMPTY")
    issues_by_id = {issue["id"]: issue}
    invocation_counter = tmp_path / "invocation_count.txt"

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "stub-cycle")
    monkeypatch.setattr(linear, "pending_issues", lambda c: linear._plan([issue]))
    monkeypatch.setattr(linear, "get_issue", lambda iid: issues_by_id[iid])
    monkeypatch.setattr(linear, "set_state", lambda iid, s: None)

    # Script: exits without committing or marking Done, tracks invocation count
    script = tmp_path / "fake-claude.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'count=$(cat "{invocation_counter}" 2>/dev/null || echo 0)\n'
        'count=$((count + 1))\n'
        f'printf "%s" "$count" > "{invocation_counter}"\n'
        "exit 0\n"
    )
    script.chmod(0o755)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(script)])

    exit_code = orchestrator.run(_stub_repos(repo), no_stack=True)

    assert exit_code != 0
    # Only one invocation — no finishing sub-agent spawned
    assert int(invocation_counter.read_text()) == 1
    payload = _read_run_log(tmp_path, "stub-cycle")
    entry = payload["entries"][0]
    assert entry["finishing_runs"] == []
    assert "finishing sub-agent" not in (entry["halt_reason"] or "")


# ---------------------------------------------------------------------------
# not-Done: no recovery when verifier verdict is FAIL
# ---------------------------------------------------------------------------


def test_no_finishing_when_verifier_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not-Done + FAIL verdict + commits → no finishing sub-agent (KR2 guard)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    issue = _issue("ABA-VFAIL")
    issues_by_id = {issue["id"]: issue}
    counter_file = tmp_path / "invocation_count.txt"

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "stub-cycle")
    monkeypatch.setattr(linear, "pending_issues", lambda c: linear._plan([issue]))
    monkeypatch.setattr(linear, "get_issue", lambda iid: issues_by_id[iid])
    monkeypatch.setattr(linear, "set_state", lambda iid, s: None)

    script = _write_commit_with_fail_verdict_script(tmp_path)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(script)])

    exit_code = orchestrator.run(_stub_repos(repo), no_stack=True)

    assert exit_code != 0
    # Only one invocation — verifier FAIL blocked recovery
    assert int(counter_file.read_text()) == 1
    payload = _read_run_log(tmp_path, "stub-cycle")
    entry = payload["entries"][0]
    assert entry["finishing_runs"] == []


# ---------------------------------------------------------------------------
# stack-no-PRs recovery: finishing sub-agent writes pr_urls → baton extends
# ---------------------------------------------------------------------------


def test_finishing_sub_agent_recovers_stack_no_prs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Done + no pr_urls + commits → finishing sub-agent writes pr_urls → continues."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    issue = _issue("ABA-STACK")
    issues_by_id = {issue["id"]: issue}
    done_marker = tmp_path / "done-identifiers.txt"

    def fake_get_issue(issue_id: str) -> dict:
        base = issues_by_id[issue_id]
        if base["identifier"] in _completed_identifiers(done_marker):
            return {**base, "state": {"type": "completed", "name": "Done"}}
        return base

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "stub-cycle")
    monkeypatch.setattr(linear, "pending_issues", lambda c: linear._plan([issue]))
    monkeypatch.setattr(linear, "get_issue", fake_get_issue)
    monkeypatch.setattr(linear, "set_state", lambda iid, s: None)

    script = _write_done_no_prs_then_finishing_script(tmp_path, done_marker)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(script)])

    # Stack mode is the default (no no_stack)
    exit_code = orchestrator.run(_stub_repos(repo))

    assert exit_code == 0
    payload = _read_run_log(tmp_path, "stub-cycle")
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["final_linear_state"] == "Done"
    assert entry["halt_reason"] is None
    assert len(entry["finishing_runs"]) == 1
    assert entry["finishing_runs"][0]["trigger"] == "err-stack-no-prs"


# ---------------------------------------------------------------------------
# stack-no-PRs recovery: finishing sub-agent still leaves no pr_urls → halt
# ---------------------------------------------------------------------------


def test_finishing_sub_agent_stack_no_prs_halts_with_enriched_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Done + no pr_urls + finishing also no pr_urls → halt names the attempt."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    issue = _issue("ABA-NOPR")
    issues_by_id = {issue["id"]: issue}
    done_marker = tmp_path / "done-identifiers.txt"
    counter_file = tmp_path / "invocation_count.txt"

    def fake_get_issue(issue_id: str) -> dict:
        base = issues_by_id[issue_id]
        if base["identifier"] in _completed_identifiers(done_marker):
            return {**base, "state": {"type": "completed", "name": "Done"}}
        return base

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "stub-cycle")
    monkeypatch.setattr(linear, "pending_issues", lambda c: linear._plan([issue]))
    monkeypatch.setattr(linear, "get_issue", fake_get_issue)
    monkeypatch.setattr(linear, "set_state", lambda iid, s: None)

    # Script: first invocation marks Done (with a commit) but writes no handoff.
    # Second invocation exits without writing handoff either.
    script = tmp_path / "fake-claude.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'count=$(cat "{counter_file}" 2>/dev/null || echo 0)\n'
        'count=$((count + 1))\n'
        f'printf "%s" "$count" > "{counter_file}"\n'
        'if [ "$count" -eq 1 ]; then\n'
        # First invocation: make commit + mark Done but no handoff
        '  git config user.email "test@test.com" 2>/dev/null\n'
        '  git config user.name "Test" 2>/dev/null\n'
        '  touch work.txt\n'
        "  git add work.txt 2>/dev/null\n"
        '  git commit -m "work" 2>/dev/null\n'
        f'  printf "%s\\n" "$(basename "$PWD")" >> "{done_marker}"\n'
        "fi\n"
        "exit 0\n"
    )
    script.chmod(0o755)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(script)])

    exit_code = orchestrator.run(_stub_repos(repo))

    assert exit_code != 0
    stderr = capsys.readouterr().err
    halt_lines = [l for l in stderr.splitlines() if l.startswith("Halt: ")]
    assert len(halt_lines) == 1
    assert "finishing sub-agent attempted" in halt_lines[0]

    payload = _read_run_log(tmp_path, "stub-cycle")
    entry = payload["entries"][0]
    assert "finishing sub-agent attempted" in entry["halt_reason"]
    assert len(entry["finishing_runs"]) == 1
    assert entry["finishing_runs"][0]["trigger"] == "err-stack-no-prs"


# ---------------------------------------------------------------------------
# One-attempt guard: not-Done recovery already ran, stack-no-PRs skips spawn
# ---------------------------------------------------------------------------


def test_finishing_sub_agent_guard_prevents_double_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If not-Done recovery ran but still not Done, the stack-no-PRs gate
    does not spawn a second finishing agent for the same issue."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    issue = _issue("ABA-GUARD")
    issues_by_id = {issue["id"]: issue}
    done_marker = tmp_path / "done-identifiers.txt"
    counter_file = tmp_path / "invocation_count.txt"

    def fake_get_issue(issue_id: str) -> dict:
        base = issues_by_id[issue_id]
        if base["identifier"] in _completed_identifiers(done_marker):
            return {**base, "state": {"type": "completed", "name": "Done"}}
        return base

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "stub-cycle")
    monkeypatch.setattr(linear, "pending_issues", lambda c: linear._plan([issue]))
    monkeypatch.setattr(linear, "get_issue", fake_get_issue)
    monkeypatch.setattr(linear, "set_state", lambda iid, s: None)

    # Script: always commits and marks Done without writing pr_urls.
    # If the guard works, this runs at most twice (main + one finishing attempt);
    # without the guard, it would run a third time at the stack-no-PRs site.
    script = tmp_path / "fake-claude.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'count=$(cat "{counter_file}" 2>/dev/null || echo 0)\n'
        'count=$((count + 1))\n'
        f'printf "%s" "$count" > "{counter_file}"\n'
        # Always commit
        'git config user.email "test@test.com" 2>/dev/null\n'
        'git config user.name "Test" 2>/dev/null\n'
        'printf "%s" "$count" > work.txt\n'
        "git add work.txt 2>/dev/null\n"
        'git commit -m "work $count" 2>/dev/null\n'
        # Second invocation onwards: mark Done (but still no pr_urls)
        'if [ "$count" -ge 2 ]; then\n'
        f'  printf "%s\\n" "$(basename "$PWD")" >> "{done_marker}"\n'
        "fi\n"
        "exit 0\n"
    )
    script.chmod(0o755)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(script)])

    # Stack mode so the stack-no-PRs gate fires
    exit_code = orchestrator.run(_stub_repos(repo))

    assert exit_code != 0
    # Only two invocations: main worker + one finishing attempt. The guard
    # prevents a third invocation at the stack-no-PRs gate.
    assert int(counter_file.read_text()) == 2


# ---------------------------------------------------------------------------
# finishing_runs field structure
# ---------------------------------------------------------------------------


def test_finishing_runs_recorded_with_model_and_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """finishing_runs entry carries model, timestamps, and trigger."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    issue = _issue("ABA-FLOG")
    issues_by_id = {issue["id"]: issue}
    done_marker = tmp_path / "done-identifiers.txt"

    def fake_get_issue(issue_id: str) -> dict:
        base = issues_by_id[issue_id]
        if base["identifier"] in _completed_identifiers(done_marker):
            return {**base, "state": {"type": "completed", "name": "Done"}}
        return base

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "stub-cycle")
    monkeypatch.setattr(linear, "pending_issues", lambda c: linear._plan([issue]))
    monkeypatch.setattr(linear, "get_issue", fake_get_issue)
    monkeypatch.setattr(linear, "set_state", lambda iid, s: None)

    # Not-Done recovery: main worker commits + doesn't mark Done; finishing marks Done
    script = _write_two_invocation_script(tmp_path, done_marker)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(script)])

    exit_code = orchestrator.run(_stub_repos(repo), no_stack=True)

    assert exit_code == 0
    payload = _read_run_log(tmp_path, "stub-cycle")
    entry = payload["entries"][0]
    assert len(entry["finishing_runs"]) == 1
    fr = entry["finishing_runs"][0]
    assert fr["trigger"] == "err-issue-not-done"
    assert fr["model"] == orchestrator._FINISHING_MODEL
    # Timestamps are ISO-8601 strings
    from datetime import datetime
    datetime.fromisoformat(fr["started_at"])
    datetime.fromisoformat(fr["finished_at"])
    assert fr["started_at"] <= fr["finished_at"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _completed_identifiers(marker: Path) -> set[str]:
    if not marker.exists():
        return set()
    return {line for line in marker.read_text().splitlines() if line}
