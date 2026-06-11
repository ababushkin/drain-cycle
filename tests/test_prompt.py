"""Prompt-template assertions.

The prompt is what the orchestrator hands to ``claude -p`` — its four
segments (title, body, execution preamble, tail line) must appear in the
documented order or the spawned agent loses the context it needs to
complete the issue and self-transition Linear. These tests pin the
ordering and the load-bearing substrings so a future refactor cannot
silently reshape the contract.
"""
from __future__ import annotations

from pathlib import Path

from drain_cycle.prompt import _STACK_TAIL, _TAIL, build, is_verify_flow


def _fixture_issue() -> dict:
    return {
        "id": "id-ABA-999",
        "identifier": "ABA-999",
        "title": "Fixture title — drain a trivial issue",
        "description": "Fixture body.\n\nMultiple paragraphs preserved verbatim.",
        "priority": 3,
        "sortOrder": 1.0,
        "state": {"type": "unstarted", "name": "Todo"},
    }


def _positions(text: str, *needles: str) -> list[int]:
    """Return the index of each needle, asserting each one is present."""
    found = []
    for needle in needles:
        idx = text.find(needle)
        assert idx != -1, f"missing segment: {needle!r}\n--- prompt ---\n{text}"
        found.append(idx)
    return found


def test_prompt_contains_four_segments_in_order(tmp_path: Path) -> None:
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree)

    title_idx, body_idx, preamble_idx, tail_idx = _positions(
        rendered,
        f"# {issue['title']}",
        issue["description"],
        "Execution instructions:",
        _TAIL,
    )
    assert title_idx < body_idx < preamble_idx < tail_idx


def test_preamble_names_worktree_base_branch_and_mcp_call(tmp_path: Path) -> None:
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree)

    # Each of the three preamble facts the spawned agent needs to act on.
    assert str(worktree) in rendered
    assert "Base branch: main" in rendered
    assert "mcp__claude_ai_Linear__save_issue" in rendered
    assert 'state: "Done"' in rendered
    # The Linear MCP call needs to know which issue — include the identifier.
    assert issue["identifier"] in rendered


def test_tail_line_is_the_last_non_empty_line(tmp_path: Path) -> None:
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree)

    non_empty = [line for line in rendered.splitlines() if line.strip()]
    assert non_empty[-1] == _TAIL


def test_resumed_prompt_inserts_directive_above_execution_instructions(
    tmp_path: Path,
) -> None:
    """``resumed=True`` adds a resume directive that leads the preamble
    so the agent reads it before the execution procedure, while ``_TAIL``
    stays the last non-empty line (the four-segment ordering is
    load-bearing)."""
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree, resumed=True)

    title_idx, body_idx, sep_idx, directive_idx, exec_idx, tail_idx = _positions(
        rendered,
        f"# {issue['title']}",
        issue["description"],
        "---",
        "Resuming issue",
        "Execution instructions:",
        _TAIL,
    )
    assert title_idx < body_idx < sep_idx < directive_idx < exec_idx < tail_idx

    # Directive names the issue and the two read-state commands the
    # agent should run before continuing.
    assert issue["identifier"] in rendered[directive_idx:exec_idx]
    assert "git log --oneline main..HEAD" in rendered
    assert "git status" in rendered

    # _TAIL is still the last non-empty line — the prepend must not
    # displace it from the trailing position.
    non_empty = [line for line in rendered.splitlines() if line.strip()]
    assert non_empty[-1] == _TAIL


def test_unresumed_prompt_is_byte_identical_to_no_kwarg_default(
    tmp_path: Path,
) -> None:
    """``resumed=False`` (the default) keeps the prompt unchanged: every
    existing call site renders byte-identical output, and the resume
    directive is absent."""
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]

    default = build(issue, worktree)
    explicit_false = build(issue, worktree, resumed=False)

    assert default == explicit_false
    assert "Resuming issue" not in default


def test_empty_description_does_not_break_rendering(tmp_path: Path) -> None:
    issue = _fixture_issue()
    issue["description"] = None  # Linear can return null descriptions
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree)

    # Title and preamble must still render in order even with no body.
    title_idx, preamble_idx, tail_idx = _positions(
        rendered,
        f"# {issue['title']}",
        "Execution instructions:",
        _TAIL,
    )
    assert title_idx < preamble_idx < tail_idx


def test_stack_false_is_byte_identical_to_default(tmp_path: Path) -> None:
    """``stack=False`` (the default) leaves the prompt byte-identical to
    a call with no ``stack`` kwarg — existing call sites are unaffected."""
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]

    default = build(issue, worktree)
    explicit_false = build(issue, worktree, stack=False)

    assert default == explicit_false


def test_stack_true_omits_push_and_drives_pr_finishing_skill(
    tmp_path: Path,
) -> None:
    """``stack=True`` replaces push-to-main with slice-commits plus a
    ``pr-finishing`` skill invocation that owns submission, and uses the
    stack tail."""
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree, stack=True)

    assert "push to main" not in rendered
    # The skill owns submission and the handoff file — the worker is told to
    # invoke it, not to hand-author a PR body or write the handoff itself.
    assert "/shape:pr-finishing" in rendered
    assert ".drain-handoff.json" in rendered
    assert "pr_urls" in rendered

    # Stack tail is used, not the normal tail.
    assert _STACK_TAIL in rendered
    assert _TAIL not in rendered

    # Stack tail is still the last non-empty line.
    non_empty = [line for line in rendered.splitlines() if line.strip()]
    assert non_empty[-1] == _STACK_TAIL


def test_base_defaults_to_main_byte_identical(tmp_path: Path) -> None:
    """Omitting ``base`` (or passing ``"main"``) renders byte-identical
    output to the no-kwarg default in both stack and push mode — existing
    call sites are unaffected by the new parameter."""
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]

    assert build(issue, worktree) == build(issue, worktree, base="main")
    assert build(issue, worktree, stack=True) == build(
        issue, worktree, stack=True, base="main"
    )
    assert build(issue, worktree, resumed=True) == build(
        issue, worktree, resumed=True, base="main"
    )


def test_chained_base_replaces_main_in_stack_prompt(tmp_path: Path) -> None:
    """A non-``main`` base anchors the stack prompt: the base-branch line,
    the pr-finishing slice instruction, and the resume range all name it."""
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree, stack=True, resumed=True, base="ABA-386")

    assert "- Base branch: ABA-386" in rendered
    assert "Base branch: main" not in rendered
    # pr-finishing is told the slices stack on the chained base.
    assert "stacked on `ABA-386`" in rendered
    assert "ABA-386..HEAD" in rendered
    # Resume range is anchored at the chained base, not main.
    assert "git log --oneline ABA-386..HEAD" in rendered

    # Four-segment ordering and stack tail are preserved.
    non_empty = [line for line in rendered.splitlines() if line.strip()]
    assert non_empty[-1] == _STACK_TAIL


def test_chained_base_in_push_mode_sets_base_line_only(tmp_path: Path) -> None:
    """A non-``main`` base in push mode updates the base-branch line but
    adds no stack/pr-finishing slice clause (push mode has no skill step)."""
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree, base="ABA-386")

    assert "- Base branch: ABA-386" in rendered
    assert "stacked on `ABA-386`" not in rendered


def test_verify_flow_detection(tmp_path: Path) -> None:
    """``is_verify_flow`` returns True only when the issue carries the ``verify`` label."""
    issue = _fixture_issue()
    assert not is_verify_flow(issue)

    issue["labels"] = ["verify"]
    assert is_verify_flow(issue)

    issue["labels"] = ["repo:my-repo", "verify", "model:sonnet"]
    assert is_verify_flow(issue)

    issue["labels"] = ["repo:my-repo", "model:sonnet"]
    assert not is_verify_flow(issue)

    issue["labels"] = []
    assert not is_verify_flow(issue)


def test_verify_flow_prompt_includes_shape_task_directive(tmp_path: Path) -> None:
    """A verify-labelled issue gets a shaping-step segment that precedes
    the execution instructions and names the issue identifier."""
    issue = {**_fixture_issue(), "labels": ["verify"]}
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree)

    # Shape-task segment is present and references the identifier.
    assert "/shape:task" in rendered
    assert issue["identifier"] in rendered
    assert "TASK-SHAPER-START" in rendered

    # Segment appears before "Execution instructions:" and tail stays last.
    shape_idx, exec_idx, tail_idx = _positions(
        rendered,
        "Shaping step",
        "Execution instructions:",
        _TAIL,
    )
    assert shape_idx < exec_idx < tail_idx

    non_empty = [line for line in rendered.splitlines() if line.strip()]
    assert non_empty[-1] == _TAIL


def test_non_verify_flow_prompt_omits_shape_task_directive(tmp_path: Path) -> None:
    """Issues without the ``verify`` label must not include the shaping step."""
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree)

    assert "/shape:task" not in rendered
    assert "Shaping step" not in rendered
    assert "TASK-SHAPER-START" not in rendered


def test_verify_flow_resumed_prompt_ordering(tmp_path: Path) -> None:
    """Resumed verify-flow prompt: resume directive < shape-task directive <
    execution instructions < tail."""
    issue = {**_fixture_issue(), "labels": ["verify"]}
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree, resumed=True)

    resume_idx, shape_idx, exec_idx, tail_idx = _positions(
        rendered,
        "Resuming issue",
        "Shaping step",
        "Execution instructions:",
        _TAIL,
    )
    assert resume_idx < shape_idx < exec_idx < tail_idx

    non_empty = [line for line in rendered.splitlines() if line.strip()]
    assert non_empty[-1] == _TAIL


def test_stack_true_four_segments_in_order(tmp_path: Path) -> None:
    """Stack prompt still keeps title → body → preamble → tail ordering."""
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree, stack=True)

    title_idx, body_idx, preamble_idx, tail_idx = _positions(
        rendered,
        f"# {issue['title']}",
        issue["description"],
        "Execution instructions:",
        _STACK_TAIL,
    )
    assert title_idx < body_idx < preamble_idx < tail_idx
