"""``drain-cycle`` CLI entry point.

Zero-arg invocation drains the current Linear cycle. Each issue's target
repo is resolved from a ``repo:<name>`` label against
``~/.drain-cycle/repos.yml``; the operator runs ``drain-cycle`` from
anywhere, not from inside a target repo. The ``scorecard`` subcommand reads
the run logs and reports per-run quality.

Secrets load before any module reads ``os.environ``, first hit wins:
shell-exported vars → ``~/.drain-cycle/.env`` → the drain-cycle repo
root ``.env`` (dev-checkout fallback, absent once installed as a uv
tool). ``load_dotenv`` defaults to ``override=False``, so an
already-set var always beats a later source and the shell always wins.

``repos.yml`` and the optional ``limits.yml`` are validated eagerly at
startup so a broken config halts exit 1 on stderr before any Linear
traffic or run-log file is written — there is no cycle yet to log
against. ``limits.yml`` is optional (its absence yields the baked-in
guardrail defaults); a present-but-malformed one still halts.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from . import limits, orchestrator, repos, scorecard, telemetry

_REPO_ENV = Path(__file__).resolve().parent.parent / ".env"


def _load_secrets() -> None:
    """Populate ``os.environ`` from the first ``.env`` that defines each key.

    ``~/.drain-cycle/.env`` sits beside ``repos.yml`` and the run logs,
    so an installed tool finds its secret there. The repo-root ``.env``
    is a dev-checkout fallback only — once installed as a uv tool the
    package lives in an isolated env where that path has no ``.env``.
    ``$HOME`` is resolved per call so tests can redirect it.
    """
    load_dotenv(Path.home() / ".drain-cycle" / ".env")
    load_dotenv(_REPO_ENV)


_USAGE = (
    "usage: drain-cycle [--watch|-w] [--no-stack]  drain the current Linear cycle\n"
    "       drain-cycle scorecard                   report per-run quality from run logs\n"
    "       drain-cycle status                      show status of the active run\n"
    "       drain-cycle --help\n"
    "\n"
    "options:\n"
    "  --watch, -w      open a tmux split-pane per issue running the live\n"
    "                   claude session (requires running inside tmux)\n"
    "  --no-stack       push to main instead of stacking PRs (default: stack)"
)

_WATCH_FLAGS = frozenset(["--watch", "-w"])
_NO_STACK_FLAGS = frozenset(["--no-stack"])
_PROJECT_FLAG = "--project"


def _parse_argv(
    argv: list[str],
) -> tuple[bool, bool, str | None, list[str]]:
    """Return (watch, no_stack, project, remaining) from raw sys.argv[1:]."""
    watch = False
    no_stack = False
    project: str | None = None
    remaining: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in _WATCH_FLAGS:
            watch = True
        elif a in _NO_STACK_FLAGS:
            no_stack = True
        elif a == _PROJECT_FLAG and i + 1 < len(argv):
            project = argv[i + 1]
            i += 1
        elif a.startswith(_PROJECT_FLAG + "="):
            project = a[len(_PROJECT_FLAG) + 1:]
        else:
            remaining.append(a)
        i += 1
    return watch, no_stack, project, remaining


def main() -> None:
    import os

    _load_secrets()
    # Telemetry reads its key from the environment just populated above; a
    # no-op when HONEYCOMB_API_KEY is unset, so an unconfigured run is
    # unchanged. Registers its own atexit flush, hence no teardown here.
    telemetry.setup()
    argv = sys.argv[1:]

    watch, no_stack, project, remaining = _parse_argv(argv)

    if not remaining:
        if watch and not os.environ.get("TMUX"):
            print(
                "drain-cycle: --watch ignored: $TMUX not set — run inside tmux to "
                "watch the live session; falling back to normal execution",
                file=sys.stderr,
            )
        try:
            loaded_repos = repos.load()
            loaded_limits = limits.load()
        except (repos.RepoConfigError, limits.LimitsConfigError) as exc:
            print(f"drain-cycle: {exc}", file=sys.stderr)
            sys.exit(1)
        sys.exit(orchestrator.run(loaded_repos, loaded_limits, watch=watch, no_stack=no_stack, project=project))

    if remaining == ["scorecard"] and not watch:
        sys.exit(scorecard.run(scorecard.runs_dir()))
    if remaining == ["status"] and not watch:
        from . import status
        sys.exit(status.run())
    if remaining == ["_stop-guard"] and not watch:
        from . import stop_guard
        sys.exit(stop_guard.run(sys.stdin, sys.stdout))
    if argv in (["-h"], ["--help"]):
        print(_USAGE)
        sys.exit(0)
    print(f"drain-cycle: unknown invocation: {' '.join(argv)}", file=sys.stderr)
    print(_USAGE, file=sys.stderr)
    sys.exit(2)
