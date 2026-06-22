"""Project-mode drain pins.

The orchestrator's ``project`` parameter overloads the active-cycle path:
the resolved project id flows through the existing ``cycle_id`` local, so
the run-log filename, console rule, telemetry attribute, and resume glob
all key on the project id. These tests pin that thread and the resolve
error path.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from drain_cycle import console, linear, orchestrator, repos


class _RecordingSpan:
    """A drop-in stand-in for an OpenTelemetry span that records attribute
    writes. Used to assert on attributes set during ``orchestrator._run``
    without bringing up a real provider."""

    def __init__(self) -> None:
        self.attrs: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attrs[key] = value

    def set_status(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        pass

    def record_exception(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        pass


def _issue(identifier: str, sort_order: float, *, repo_name: str = "test-repo") -> dict:
    return {
        "id": f"id-{identifier}",
        "identifier": identifier,
        "title": f"Title for {identifier}",
        "description": f"Body for {identifier}",
        "sortOrder": sort_order,
        "state": {"type": "unstarted", "name": "Todo"},
        "labels": [f"repo:{repo_name}"],
    }


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)


def _completed_identifiers(marker: Path) -> set[str]:
    if not marker.exists():
        return set()
    return {line for line in marker.read_text().splitlines() if line}


def _write_fake_claude_script(tmp_path: Path, done_marker: Path) -> Path:
    script = tmp_path / "fake-claude.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$(basename "$PWD")" >> "{done_marker}"\n'
    )
    script.chmod(0o755)
    return script


def test_project_mode_writes_runlog_keyed_on_project_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end skeleton: ``run(project=…)`` resolves the id, fetches via
    ``project_issues``, and writes a run log whose identity equals the project
    id — proof the resolved id threads through ``cycle_id`` to the on-disk
    artefact instead of the active cycle id."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("HOME", str(tmp_path))

    only = _issue("ABA-ONE", sort_order=1.0)
    issues_by_id = {only["id"]: only}
    done_marker = tmp_path / "done-identifiers.txt"

    def fake_resolve_project_id(name_or_id: str) -> str:
        assert name_or_id == "stub-project-name"
        return "stub-project-id"

    project_calls: list[str] = []

    def fake_project_issues(project_id: str):
        project_calls.append(project_id)
        completed = _completed_identifiers(done_marker)
        return linear._plan(
            [i for i in [only] if i["identifier"] not in completed]
        )

    def fake_current_cycle_id() -> str:
        raise AssertionError("project mode must not fetch the active cycle id")

    def fake_pending_issues(_cycle_id: str):  # pragma: no cover
        raise AssertionError("project mode must not call pending_issues")

    def fake_get_issue(issue_id: str) -> dict:
        issue = issues_by_id[issue_id]
        if issue["identifier"] in _completed_identifiers(done_marker):
            return {**issue, "state": {"type": "completed", "name": "Done"}}
        return issue

    monkeypatch.setattr(linear, "resolve_project_id", fake_resolve_project_id)
    monkeypatch.setattr(linear, "project_issues", fake_project_issues)
    monkeypatch.setattr(linear, "current_cycle_id", fake_current_cycle_id)
    monkeypatch.setattr(linear, "pending_issues", fake_pending_issues)
    monkeypatch.setattr(linear, "get_issue", fake_get_issue)
    monkeypatch.setattr(linear, "set_state", lambda issue_id, state_name: None)

    fake_claude = _write_fake_claude_script(tmp_path, done_marker)
    monkeypatch.setattr(orchestrator, "_CLAUDE_CMD", [str(fake_claude)])

    exit_code = orchestrator.run(
        repos.Repos(mapping={"test-repo": repo}),
        no_stack=True,
        project="stub-project-name",
    )
    assert exit_code == 0
    assert project_calls == ["stub-project-id"]

    runs_dir = tmp_path / ".drain-cycle" / "runs"
    log_files = list(runs_dir.glob("stub-project-id-*.json"))
    assert len(log_files) == 1, (
        f"expected one run log keyed on the project id, found: "
        f"{[p.name for p in runs_dir.iterdir()]}"
    )
    payload = json.loads(log_files[0].read_text())
    assert payload["cycle_id"] == "stub-project-id"


def test_project_mode_empty_plan_message_reads_project_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ``nothing to do`` message reads ``Project <id>`` in project mode.
    The rule itself isn't rendered when the plan is empty; the cycle/project
    label on the rule is covered separately via ``console.startup_plan``.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(linear, "resolve_project_id", lambda name: "proj-empty")
    monkeypatch.setattr(
        linear, "project_issues",
        lambda _id: linear.ExecutionPlan(order=[], deferred=[]),
    )

    span = _RecordingSpan()
    exit_code = orchestrator._run(
        repos.Repos(mapping={}),
        orchestrator.Limits(),
        span,  # type: ignore[arg-type]
        project="My Project",
    )
    assert exit_code == 0
    err = capsys.readouterr().err
    assert "Project proj-empty has no Todo/Backlog issues" in err
    assert "Cycle proj-empty" not in err


def test_cycle_mode_empty_plan_message_reads_cycle_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cycle mode (the default) still reads ``Cycle <id>``."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(linear, "current_cycle_id", lambda: "cyc-empty")
    monkeypatch.setattr(
        linear, "pending_issues",
        lambda _id: linear.ExecutionPlan(order=[], deferred=[]),
    )

    span = _RecordingSpan()
    exit_code = orchestrator._run(
        repos.Repos(mapping={}),
        orchestrator.Limits(),
        span,  # type: ignore[arg-type]
    )
    assert exit_code == 0
    err = capsys.readouterr().err
    assert "Cycle cyc-empty has no Todo/Backlog issues" in err


def test_startup_plan_renders_project_label_when_target_kind_is_project(
    capsys: pytest.CaptureFixture[str],
) -> None:
    console.startup_plan(
        "proj-xyz",
        [("ABA-1", "first thing", "sonnet-4")],
        target_kind="project",
    )
    err = capsys.readouterr().err
    assert "project proj-xyz" in err
    # The cycle label must not leak when the target is a project.
    assert "cycle proj-xyz" not in err


def test_drain_target_kind_span_attribute_records_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(linear, "resolve_project_id", lambda name: "proj-1")
    monkeypatch.setattr(
        linear, "project_issues",
        lambda _id: linear.ExecutionPlan(order=[], deferred=[]),
    )

    span = _RecordingSpan()
    orchestrator._run(
        repos.Repos(mapping={}),
        orchestrator.Limits(),
        span,  # type: ignore[arg-type]
        project="My Project",
    )
    assert span.attrs["drain.target_kind"] == "project"
    assert span.attrs["drain.cycle_id"] == "proj-1"


def test_drain_target_kind_span_attribute_records_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(linear, "current_cycle_id", lambda: "cyc-1")
    monkeypatch.setattr(
        linear, "pending_issues",
        lambda _id: linear.ExecutionPlan(order=[], deferred=[]),
    )

    span = _RecordingSpan()
    orchestrator._run(
        repos.Repos(mapping={}),
        orchestrator.Limits(),
        span,  # type: ignore[arg-type]
    )
    assert span.attrs["drain.target_kind"] == "cycle"
    assert span.attrs["drain.cycle_id"] == "cyc-1"
