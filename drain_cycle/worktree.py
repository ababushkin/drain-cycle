"""Thin wrapper around ``git worktree``.

Each issue gets ``.worktrees/<issue-identifier>/`` branched off ``main``,
used once, then removed on Done — or preserved on halt so a later re-run
can resume against the committed work (see
``docs/adrs/0018-resume-on-rerun.md``).

``git worktree`` stderr is captured and surfaced in the raised
``RuntimeError`` on failure. The orchestrator's pre-spawn try/except
threads the message into the runlog's ``halt_reason`` so the operator
sees git's actual diagnostic (dirty tree, branch already exists,
missing ``main``) rather than just a non-zero exit code.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import telemetry

BASE_BRANCH = "main"
WORKTREE_DIR = ".worktrees"
# Records the branch a worktree was forked from, so a later run that reuses a
# preserved worktree (a resumed halt) recovers the true base rather than
# recomputing it from the in-memory chain baton, which is empty across runs.
BASE_FILE = ".drain-base"


@dataclass(frozen=True)
class WorktreeHandle:
    """A prepared worktree, together with whether it was reused.

    ``resumed`` is ``True`` when ``ensure`` found a pre-existing worktree
    registered at the expected path (typically left behind by an earlier
    halted run). Callers thread the flag into the spawn-time prompt so
    the agent knows it is continuing from prior committed work rather
    than starting fresh.
    """

    path: Path
    resumed: bool


def add(repo: Path, identifier: str, base: str = BASE_BRANCH) -> Path:
    """Create a worktree branched off ``base`` for ``identifier``.

    Returns the absolute path to the new worktree.
    """
    worktree_path = repo / WORKTREE_DIR / identifier
    with telemetry.tracer.start_as_current_span("drain.worktree.add") as span:
        span.set_attribute("worktree.identifier", identifier)
        span.set_attribute("worktree.repo", repo.name)
        span.set_attribute("worktree.path", str(worktree_path))
        span.set_attribute("worktree.base", base)
        _run_git(
            ["worktree", "add", "-b", identifier, str(worktree_path), base],
            cwd=repo,
        )
    (worktree_path / BASE_FILE).write_text(f"{base}\n")
    return worktree_path


def ensure(repo: Path, identifier: str, base: str = BASE_BRANCH) -> WorktreeHandle:
    """Reuse a preserved worktree if one is already registered, else add.

    A worktree registered at ``repo/.worktrees/<identifier>`` is reused
    as-is — no mutating git command is run, so a dirty index, staged or
    untracked files, and the gitignored config symlinks all survive
    untouched. Any other state (no entry at that path) falls through to
    :func:`add`, whose ``RuntimeError`` on a leftover branch or orphan
    directory is what the orchestrator's existing pre-spawn handler
    turns into the clean ``Halt: … — setup failed: …`` line.
    """
    worktree_path = repo / WORKTREE_DIR / identifier
    with telemetry.tracer.start_as_current_span("drain.worktree.ensure") as span:
        span.set_attribute("worktree.identifier", identifier)
        span.set_attribute("worktree.repo", repo.name)
        span.set_attribute("worktree.path", str(worktree_path))
        if _is_registered_worktree(repo, worktree_path):
            span.set_attribute("worktree.resumed", True)
            handle = WorktreeHandle(path=worktree_path, resumed=True)
        else:
            span.set_attribute("worktree.resumed", False)
            handle = WorktreeHandle(path=add(repo, identifier, base), resumed=False)
    # Trust the worktree's mise config on both paths: a fresh checkout's tracked
    # mise.toml is untrusted (path-keyed), and a resumed worktree from a pre-fix
    # run never got trusted either.
    _trust_mise(worktree_path)
    return handle


def read_base(worktree_path: Path) -> str:
    """Return the branch a worktree was forked from, or ``main`` if unknown.

    Reads the :data:`BASE_FILE` marker written by :func:`add`. A missing or
    empty marker (a worktree created before this marker existed, or one not
    created by :func:`add`) falls back to ``BASE_BRANCH`` — the same default
    an unchained issue would compute.
    """
    try:
        base = (worktree_path / BASE_FILE).read_text().strip()
    except OSError:
        return BASE_BRANCH
    return base or BASE_BRANCH


def link_project_config(
    repo: Path, worktree_path: Path, names: Iterable[str]
) -> list[Path]:
    """Symlink gitignored project-scoped config from ``repo`` into the worktree.

    A git worktree checks out only tracked files, so gitignored project config
    (``.claude/`` settings/hooks/agents/skills, a root ``.mcp.json``) is absent.
    Linking the repo's real entries in gives a worker the same settings, hooks,
    agents, skills, and MCP config as an interactive session at the repo root —
    and because the link points at the live dir, a stateful hook reads and
    writes the repo's actual config exactly as a non-worktree run would.

    For each name: skip it if absent in ``repo`` (a clean no-op for repos
    without that config) or if something already occupies that path in the
    worktree (a tracked entry git checked out, or a pre-existing link). The
    check uses ``os.path.lexists`` so a dangling link counts as present and is
    never clobbered.

    Exception: if the worktree path is a real directory (not a symlink), it
    was likely created by a tool running in the worktree before the symlink
    could be planted (e.g. entire.io creating ``.entire/`` on first run). In
    that case, merge any files from the worktree directory into ``source``
    (without overwriting existing files), remove the directory, and replace it
    with a symlink so future writes land in the shared location.

    Returns the links created.
    """
    created: list[Path] = []
    repo = repo.resolve()
    for name in names:
        source = repo / name
        if not source.exists():
            continue
        link = worktree_path / name
        if os.path.islink(link):
            continue
        if link.is_dir() and _is_gitignored(worktree_path, name):
            # Migrate: move files not already in source, then replace with symlink.
            for item in link.iterdir():
                dest = source / item.name
                if not dest.exists():
                    shutil.move(str(item), str(dest))
            shutil.rmtree(link)
        elif os.path.lexists(link):
            # A tracked or otherwise pre-existing entry we won't clobber.
            continue
        os.symlink(source, link)
        created.append(link)
    return created


def merge_entire_sessions(worktree_path: Path, repo: Path) -> None:
    """Merge session dirs from a worktree-local ``.entire/`` into the repo-root one.

    Called just before removal so entire.io session data written by the
    worker isn't lost. No-op if the worktree's ``.entire`` is a symlink
    (already points at the repo root) or if the repo has no ``.entire/``.
    """
    worktree_entire = worktree_path / ".entire"
    if not worktree_entire.is_dir() or os.path.islink(worktree_entire):
        return
    repo_entire = repo.resolve() / ".entire"
    if not repo_entire.exists():
        return
    for item in worktree_entire.iterdir():
        dest = repo_entire / item.name
        if not dest.exists():
            shutil.move(str(item), str(dest))


def remove(repo: Path, worktree_path: Path) -> None:
    """Remove a worktree previously created by :func:`add`."""
    with telemetry.tracer.start_as_current_span("drain.worktree.remove") as span:
        span.set_attribute("worktree.repo", repo.name)
        span.set_attribute("worktree.path", str(worktree_path))
        _run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=repo)


def _trust_mise(worktree_path: Path) -> None:
    """Best-effort ``mise trust`` run with ``worktree_path`` as the working dir.

    A git worktree checks out the repo's tracked ``mise.toml`` to a new path, but
    mise trust is path-keyed, so the copy is untrusted even when the repo root is
    trusted — every mise invocation in the worktree (e.g. a SessionEnd hook running
    git there) then errors. Running trust *in* the worktree marks its own config.

    No-op when mise is not installed, so repos that don't use mise are unaffected.
    Failures are swallowed: trust is a convenience, never a reason to fail setup.
    """
    if shutil.which("mise") is None:
        return
    subprocess.run(
        ["mise", "trust"],
        cwd=worktree_path,
        check=False,
        capture_output=True,
    )


def _is_gitignored(repo: Path, name: str) -> bool:
    """Return ``True`` if ``name`` is gitignored in ``repo``."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", name],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _is_registered_worktree(repo: Path, worktree_path: Path) -> bool:
    """Return ``True`` if git lists a worktree at ``worktree_path``.

    Parses ``git worktree list --porcelain -z`` for a ``worktree <path>``
    record matching ``worktree_path.resolve()``. ``-z`` makes each record
    NUL-separated and each field NUL-terminated, so paths with embedded
    spaces or newlines are unambiguous. The resolve step matches git's
    own canonicalisation (symlinks, ``..``) so a worktree under a
    symlinked repo path still matches the entry git printed. A non-zero
    git exit is treated as not-registered so ``ensure`` falls through to
    ``add``, whose error surfaces git's real diagnostic via the
    orchestrator's pre-spawn halt path.
    """
    target = worktree_path.resolve()
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain", "-z"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    for field in result.stdout.split("\0"):
        if not field.startswith("worktree "):
            continue
        listed = Path(field.removeprefix("worktree "))
        if listed.resolve() == target:
            return True
    return False


def _run_git(args: list[str], *, cwd: Path) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip() or '<no output>'}"
        )
