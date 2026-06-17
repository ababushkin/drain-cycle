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

from drain_cycle.prompt import _TAIL, build, build_finishing


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


def test_preamble_names_worktree_and_base_branch(tmp_path: Path) -> None:
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree)

    assert str(worktree) in rendered
    assert "Base branch: main" in rendered
    assert issue["identifier"] in rendered
    assert "/shape:exec:pickup" in rendered


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
    so the agent reads it before the execution pointer, while ``_TAIL``
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


def test_base_defaults_to_main_byte_identical(tmp_path: Path) -> None:
    """Omitting ``base`` (or passing ``"main"``) renders byte-identical
    output to the no-kwarg default — existing call sites are unaffected
    by the parameter."""
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]

    assert build(issue, worktree) == build(issue, worktree, base="main")
    assert build(issue, worktree, resumed=True) == build(
        issue, worktree, resumed=True, base="main"
    )


def test_chained_base_sets_base_line_and_resume_range(tmp_path: Path) -> None:
    """A non-``main`` base appears in the base-branch line and anchors
    the resume range."""
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree, resumed=True, base="ABA-386")

    assert "- Base branch: ABA-386" in rendered
    assert "Base branch: main" not in rendered
    assert "git log --oneline ABA-386..HEAD" in rendered

    non_empty = [line for line in rendered.splitlines() if line.strip()]
    assert non_empty[-1] == _TAIL


def test_chained_base_without_resume_sets_base_line_only(tmp_path: Path) -> None:
    """A non-``main`` base without resume updates only the base-branch line."""
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree, base="ABA-386")

    assert "- Base branch: ABA-386" in rendered
    assert "Resuming issue" not in rendered


def test_emitted_template_fits_within_line_budget(tmp_path: Path) -> None:
    """The preamble + tail section (the template, excluding dynamic issue
    content) must stay within the ≤15-line budget — the KR3 brake.

    Tested in the most verbose case (resumed with a chained base) since
    that adds the resume directive and is the longest the template gets.
    """
    issue = _fixture_issue()
    worktree = tmp_path / ".worktrees" / issue["identifier"]
    rendered = build(issue, worktree, resumed=True, base="ABA-386")

    # Extract the template section: everything from the --- separator onward.
    sep = rendered.index("---\n")
    template_section = rendered[sep:]
    assert len(template_section.splitlines()) <= 15


def test_build_finishing_contains_identifier_base_and_worktree(tmp_path: Path) -> None:
    """build_finishing names the identifier, worktree, and base in the output."""
    worktree = tmp_path / ".worktrees" / "ABA-383"
    rendered = build_finishing("ABA-383", worktree, "main")

    assert "ABA-383" in rendered
    assert str(worktree) in rendered
    assert "main..HEAD" in rendered
    assert "/shape:pr-finishing" in rendered
    assert "exec-state.json" in rendered
    assert ".drain-handoff.json" not in rendered
    # Tail line last
    non_empty = [line for line in rendered.splitlines() if line.strip()]
    assert "Done" in non_empty[-1]


def test_build_finishing_chained_base_adds_stack_clause(tmp_path: Path) -> None:
    """A non-main base in build_finishing adds the stack-clause for pr-finishing."""
    worktree = tmp_path / ".worktrees" / "ABA-383"
    rendered = build_finishing("ABA-383", worktree, "ABA-380")

    assert "stacked on `ABA-380`" in rendered
    assert "ABA-380..HEAD" in rendered
    assert "- Base branch: ABA-380" in rendered


def test_build_finishing_main_base_omits_stack_clause(tmp_path: Path) -> None:
    """build_finishing with base='main' does not add the stacked-on clause."""
    worktree = tmp_path / ".worktrees" / "ABA-383"
    rendered = build_finishing("ABA-383", worktree, "main")

    assert "stacked on" not in rendered


def test_build_finishing_mentions_opus_for_fixes(tmp_path: Path) -> None:
    """build_finishing instructs the agent to delegate fixes to opus."""
    from drain_cycle.prompt import _FINISHING_OPUS_MODEL
    worktree = tmp_path / ".worktrees" / "ABA-383"
    rendered = build_finishing("ABA-383", worktree, "main")

    assert _FINISHING_OPUS_MODEL in rendered
