"""Multi-repo orchestrator behaviour.

Two halves:

1. **Happy path** — two issues with different ``repo:`` labels are each
   resolved to their respective configured paths, and ``subprocess.run``
   sees the matching ``cwd``. Guards against a regression that wires
   ``Path.cwd()`` back in (or hardcodes one of the issues' repos for
   both).

2. **Resolution halt** — each ``RepoResolutionError`` variant must
   write a run-log entry and emit the matching ``Halt:`` line, and
   must **not** call ``linear.set_state`` (no state was moved yet).
   Behaviour contrast with the existing setup-failure halt: that one
   also doesn't revert, but it does try ``set_state`` first; here we
   never reach it.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from drain_cycle import linear, orchestrator, repos

# Shell fragment a fake stack-mode worker appends to emit the handoff a real
# `/shape:pr-finishing` run would write — the orchestrator reads its non-empty
# ``pr_urls`` as proof the issue's PRs were submitted before tearing down.
# Dual-write mirrors the migration period: exec-state.json is the primary file
# the orchestrator now reads, with .drain-handoff.json kept as the legacy copy.
# The two files differ in shape: exec-state.json keys pr_urls under the
# ``finish`` section (ADR 0030); the legacy file keeps them top-level.
_EXEC_STATE_PAYLOAD = '{\"finish\":{\"pr_urls\":[{\"title\":\"feat\",\"url\":\"https://x/pull/1\"}]}}'
_LEGACY_PAYLOAD = '{\"pr_urls\":[{\"title\":\"feat\",\"url\":\"https://x/pull/1\"}]}'
_HANDOFF_LINE = (
    f"printf '%s' '{_EXEC_STATE_PAYLOAD}' > exec-state.json\n"
    f"printf '%s' '{_LEGACY_PAYLOAD}' > .drain-handoff.json\n"
)
# Variant that writes only exec-state.json (pack has fully cut over).
_EXEC_STATE_ONLY_LINE = (
    f"printf '%s' '{_EXEC_STATE_PAYLOAD}' > exec-state.json\n"
)


def _issue(
    identifier: str,
    repo_name: str,
    *,
    sort_order: float = 1.0,
    labels: list[str] | None = None,
) -> dict:
    return {
        "id": f"id-{identifier}",
        "identifier": identifier,
        "title": f"Title for {identifier}",
        "description": f"Body for {identifier}",
        "sortOrder": sort_order,
        "state": {"type": "unstarted", "name": "Todo"},
        "labels": [f"repo:{repo_name}"] if labels is None else labels,
    }


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@x"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)


def _write_fake_claude(tmp_path: Path, marker: Path) -> Path:
    """Fake ``claude -p`` that records its cwd against its identifier
    so the test can prove the orchestrator passed the right ``cwd``."""
    script = tmp_path / "fake-claude.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\t%s\\n" "$(basename "$PWD")" "$PWD" >> "{marker}"\n'
        f"{_HANDOFF_LINE}"
    )
    script.chmod(0o755)
    return script


def test_orchestrator_runs_each_issue_in_its_labelled_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two issues, two repos, two ``repo:`` labels — each spawned
    ``claude -p`` runs in its own resolved repo's worktree."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    _init_repo(repo_a)
    _init_repo(repo_b)
    monkeypatch.setenv("HOME", str(tmp_path))

    first = _issue("ABA-A", repo_name="alpha", sort_order=1.0)
    second = _issue("ABA-B", repo_name="beta", sort_order=2.0)
    raw_issues = [first, second]
    issues_by_id = {i["id"]: i for i in raw_issues}
    marker = tmp_path / "picked.tsv"

    def fake_pending_issues(cycle_id: str):
        completed = {line.split("\t", 1)[0] for line in _lines(marker)}
        return linear._plan(
            [i for i in raw_issues if i["identifier"] not in completed]
        )

    def fake_get_issue(issue_id: str) -> dict:
        identifier = issues_by_id[issue_id]["identifier"]
        completed = {line.split("\t", 1)[0] for line in _lines(marker)}
        if identifier in completed:
            return {
                **issues_by_id[issue_id],
                "state": {"type": "completed", "name": "Done"},
            }
        return issues_by_id[issue_id]

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "cycle-id")
    monkeypatch.setattr(linear, "pending_issues", fake_pending_issues)
    monkeypatch.setattr(linear, "get_issue", fake_get_issue)
    monkeypatch.setattr(linear, "set_state", lambda issue_id, name: None)

    fake_claude = _write_fake_claude(tmp_path, marker)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(fake_claude)])

    exit_code = orchestrator.run(
        repos.Repos(mapping={"alpha": repo_a, "beta": repo_b})
    )
    assert exit_code == 0

    # The fake-claude script wrote ``<identifier>\t<cwd>`` once per pick.
    rows = [line.split("\t", 1) for line in _lines(marker)]
    by_identifier = {ident: Path(cwd) for ident, cwd in rows}
    assert by_identifier["ABA-A"] == repo_a / ".worktrees" / "ABA-A"
    assert by_identifier["ABA-B"] == repo_b / ".worktrees" / "ABA-B"

    # Each repo's worktree directory cleaned up on success.
    assert not (repo_a / ".worktrees").exists() or not any((repo_a / ".worktrees").iterdir())
    assert not (repo_b / ".worktrees").exists() or not any((repo_b / ".worktrees").iterdir())


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text().splitlines() if line]


def _write_committing_claude(tmp_path: Path, marker: Path) -> Path:
    """Fake ``claude -p`` that makes a git commit in its worktree and records
    its identifier so ``fake_get_issue`` can mark it Done."""
    script = tmp_path / "fake-claude-commit.sh"
    script.write_text(
        "#!/bin/sh\n"
        'id="$(basename "$PWD")"\n'
        'git config user.email "test@example.com"\n'
        'git config user.name "Test"\n'
        'echo "work" > work.txt\n'
        "git add work.txt\n"
        'git commit -m "work for $id"\n'
        f'printf "%s\\n" "$id" >> "{marker}"\n'
        f"{_HANDOFF_LINE}"
    )
    script.chmod(0o755)
    return script


def _git_log_hashes(path: Path, ref: str = "HEAD") -> list[str]:
    """Return commit hashes reachable from ``ref`` in ``path``."""
    result = subprocess.run(
        ["git", "log", "--format=%H", ref],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().splitlines()


def test_stack_mode_chains_same_repo_worktrees_off_prior_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In stack mode, consecutive same-repo issues chain: the second issue's
    worktree branches off the first issue's branch (a true Graphite stack),
    while an issue in a different repo still branches off its own main. Each
    completed issue's worktree is torn down normally."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    _init_repo(repo_a)
    _init_repo(repo_b)
    monkeypatch.setenv("HOME", str(tmp_path))

    first = _issue("ABA-1", repo_name="alpha", sort_order=1.0)
    second = _issue("ABA-2", repo_name="alpha", sort_order=2.0)
    other = _issue("ABA-3", repo_name="beta", sort_order=3.0)
    raw_issues = [first, second, other]
    issues_by_id = {i["id"]: i for i in raw_issues}
    marker = tmp_path / "done.txt"

    def fake_pending_issues(cycle_id: str):
        completed = {line for line in _lines(marker)}
        return linear._plan(
            [i for i in raw_issues if i["identifier"] not in completed]
        )

    def fake_get_issue(issue_id: str) -> dict:
        identifier = issues_by_id[issue_id]["identifier"]
        completed = {line for line in _lines(marker)}
        if identifier in completed:
            return {
                **issues_by_id[issue_id],
                "state": {"type": "completed", "name": "Done"},
            }
        return issues_by_id[issue_id]

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "cycle-id")
    monkeypatch.setattr(linear, "pending_issues", fake_pending_issues)
    monkeypatch.setattr(linear, "get_issue", fake_get_issue)
    monkeypatch.setattr(linear, "set_state", lambda issue_id, name: None)

    fake_claude = _write_committing_claude(tmp_path, marker)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(fake_claude)])

    exit_code = orchestrator.run(
        repos.Repos(mapping={"alpha": repo_a, "beta": repo_b}),
    )
    assert exit_code == 0

    # Worktrees are torn down after each issue completes.
    assert not (repo_a / ".worktrees" / "ABA-1").exists()
    assert not (repo_a / ".worktrees" / "ABA-2").exists()
    assert not (repo_b / ".worktrees" / "ABA-3").exists()

    # ABA-2 (same repo, runs after ABA-1) is stacked on ABA-1: ABA-1's commit
    # is an ancestor of ABA-2, so it appears in ABA-2's history.
    hashes_1 = _git_log_hashes(repo_a, "ABA-1")
    hashes_2 = _git_log_hashes(repo_a, "ABA-2")
    assert hashes_1[0] in hashes_2
    # ABA-3 is in a different repo, so it branches from that repo's main, not
    # from any alpha branch.
    main_b = _git_log_hashes(repo_b, "main")
    hashes_3 = _git_log_hashes(repo_b, "ABA-3")
    assert main_b[0] in hashes_3


def test_stack_mode_exec_state_only_handoff_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker that writes only exec-state.json (no legacy .drain-handoff.json)
    must be accepted: the orchestrator's handoff.read() prefers exec-state.json."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    issue = _issue("ABA-1", repo_name="alpha", sort_order=1.0)
    issues_by_id = {issue["id"]: issue}
    marker = tmp_path / "done.txt"

    def fake_pending_issues(cycle_id: str):
        completed = set(_lines(marker))
        return linear._plan([i for i in [issue] if i["identifier"] not in completed])

    def fake_get_issue(issue_id: str) -> dict:
        if issues_by_id[issue_id]["identifier"] in set(_lines(marker)):
            return {**issues_by_id[issue_id], "state": {"type": "completed", "name": "Done"}}
        return issues_by_id[issue_id]

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "cycle-id")
    monkeypatch.setattr(linear, "pending_issues", fake_pending_issues)
    monkeypatch.setattr(linear, "get_issue", fake_get_issue)
    monkeypatch.setattr(linear, "set_state", lambda issue_id, name: None)

    script = tmp_path / "exec-state-claude.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$(basename "$PWD")" >> "{marker}"\n'
        f"{_EXEC_STATE_ONLY_LINE}"
    )
    script.chmod(0o755)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(script)])

    exit_code = orchestrator.run(repos.Repos(mapping={"alpha": repo}))
    assert exit_code == 0


def test_stack_mode_done_without_handoff_halts_and_preserves_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stack-mode issue the worker marked Done but left no submitted
    ``pr_urls`` must halt the run rather than tear down: the orchestrator
    cannot let the next same-repo issue stack onto a branch that was never
    pushed. The worktree is preserved for inspection and the run exits
    non-zero."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    issue = _issue("ABA-1", repo_name="alpha", sort_order=1.0)
    issues_by_id = {issue["id"]: issue}
    marker = tmp_path / "done.txt"

    def fake_pending_issues(cycle_id: str):
        completed = set(_lines(marker))
        return linear._plan(
            [i for i in [issue] if i["identifier"] not in completed]
        )

    def fake_get_issue(issue_id: str) -> dict:
        if issues_by_id[issue_id]["identifier"] in set(_lines(marker)):
            return {**issues_by_id[issue_id], "state": {"type": "completed", "name": "Done"}}
        return issues_by_id[issue_id]

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "cycle-id")
    monkeypatch.setattr(linear, "pending_issues", fake_pending_issues)
    monkeypatch.setattr(linear, "get_issue", fake_get_issue)
    monkeypatch.setattr(linear, "set_state", lambda issue_id, name: None)

    # Worker marks the issue Done but writes no .drain-handoff.json.
    script = tmp_path / "no-handoff-claude.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$(basename "$PWD")" >> "{marker}"\n'
    )
    script.chmod(0o755)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(script)])

    exit_code = orchestrator.run(repos.Repos(mapping={"alpha": repo}))

    assert exit_code != 0
    # Worktree is preserved (not torn down) so the work can be inspected.
    assert (repo / ".worktrees" / "ABA-1").exists()


# ---------------------------------------------------------------------------
# Resolution-halt path. One parametrised test per error variant.
# ---------------------------------------------------------------------------


def _run_resolution_halt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    issue_labels: list[str],
    repos_mapping: dict[str, Path],
) -> tuple[int, dict, list[str]]:
    """Run the orchestrator with one issue carrying the supplied labels
    and the supplied ``Repos`` mapping. Returns ``(exit_code, runlog_payload,
    stderr_lines)``. ``linear.set_state`` is a tripwire — calling it on
    the resolution-halt path is the regression this whole test exists
    to guard against."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    issue = _issue("ABA-1", repo_name="ignored", labels=issue_labels)

    def fake_pending_issues(cycle_id: str):
        return linear._plan([issue])

    def forbidden_set_state(issue_id: str, state_name: str) -> None:
        raise AssertionError(
            "linear.set_state must NOT be called on the resolution-halt path"
        )

    def forbidden_get_issue(issue_id: str) -> dict:
        raise AssertionError(
            "linear.get_issue must NOT be called on the resolution-halt path"
        )

    monkeypatch.setattr(linear, "current_cycle_id", lambda: "cycle-id")
    monkeypatch.setattr(linear, "pending_issues", fake_pending_issues)
    monkeypatch.setattr(linear, "set_state", forbidden_set_state)
    monkeypatch.setattr(linear, "get_issue", forbidden_get_issue)

    forbidden_claude = tmp_path / "forbidden-claude.sh"
    forbidden_claude.write_text("#!/bin/sh\nexit 99\n")
    forbidden_claude.chmod(0o755)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(forbidden_claude)])

    exit_code = orchestrator.run(repos.Repos(mapping=repos_mapping))

    runs_dir = tmp_path / ".drain-cycle" / "runs"
    payloads = [json.loads(p.read_text()) for p in runs_dir.glob("cycle-id-*.json")]
    assert len(payloads) == 1
    return exit_code, payloads[0], []


def _halt_lines(captured_err: str) -> list[str]:
    return [line for line in captured_err.splitlines() if line.startswith("Halt: ")]


@pytest.mark.parametrize(
    "issue_labels, repos_mapping_factory, expected_tail",
    [
        pytest.param(
            [],
            lambda tmp_path: {"alpha": tmp_path},
            "no repo: label on issue",
            id="no-repo-label",
        ),
        pytest.param(
            ["repo:alpha", "repo:beta"],
            lambda tmp_path: {"alpha": tmp_path, "beta": tmp_path},
            "multiple repo: labels: alpha, beta",
            id="multiple-repo-labels",
        ),
        pytest.param(
            ["repo:unknown"],
            lambda tmp_path: {"alpha": tmp_path},
            'repo "unknown" not in ~/.drain-cycle/repos.yml',
            id="unknown-repo-name",
        ),
    ],
)
def test_resolution_halt_variants_emit_correct_message_and_no_set_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    issue_labels: list[str],
    repos_mapping_factory,
    expected_tail: str,
) -> None:
    exit_code, payload, _ = _run_resolution_halt(
        tmp_path,
        monkeypatch,
        issue_labels=issue_labels,
        repos_mapping=repos_mapping_factory(tmp_path),
    )
    assert exit_code == 1

    # Exactly one halt entry, with the resolution-halt placeholder path
    # and the matching tail.
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["issue_identifier"] == "ABA-1"
    assert entry["worktree_path"] == "<unresolved>"
    assert entry["final_linear_state"] == "Todo"
    assert expected_tail in entry["halt_reason"]

    halt_lines = _halt_lines(capsys.readouterr().err)
    assert len(halt_lines) == 1
    (halt_line,) = halt_lines
    assert halt_line == entry["halt_reason"]
    assert "<unresolved>" in halt_line
    assert expected_tail in halt_line


def test_resolution_halt_when_resolved_path_does_not_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mapping entry points at a missing directory — the fourth
    ``RepoResolutionError`` variant. Kept separate from the parametrised
    test because the expected tail names the absolute path the operator
    needs to fix."""
    missing = tmp_path / "missing"
    exit_code, payload, _ = _run_resolution_halt(
        tmp_path,
        monkeypatch,
        issue_labels=["repo:alpha"],
        repos_mapping={"alpha": missing},
    )
    assert exit_code == 1

    entry = payload["entries"][0]
    expected_tail = f"resolved path {missing} does not exist"
    assert expected_tail in entry["halt_reason"]

    halt_lines = _halt_lines(capsys.readouterr().err)
    (halt_line,) = halt_lines
    assert halt_line == entry["halt_reason"]
    assert "<unresolved>" in halt_line
    assert expected_tail in halt_line
