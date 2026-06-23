"""Version derived from the git checkout.

The package declares no static version. Both the build backend (see
``hatch_build.py``) and the running CLI compute the number from commit history,
so it tracks source state without a tag, a manual bump, or a per-commit edit.

Two shapes:

* :func:`public_version` — ``BASE.<commit-count>``. Written into installed
  metadata at build time, so ``uv tool list`` and ``importlib.metadata`` report
  it. Monotonic on a linear ``main`` history.
* :func:`full_version` — the public version plus the short commit SHA and a
  ``.dirty`` marker for an unclean tree. Used for live reporting (``--version``
  and the Honeycomb ``service.version``) where the exact state matters.

When git or the checkout is unavailable — a wheel installed into ``site-packages``
with no ``.git`` alongside it — both fall back to :data:`_FALLBACK` rather than
failing the build or the CLI.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

BASE = "0.1"
_FALLBACK = f"{BASE}.0"
_ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ("git", "-C", str(_ROOT), *args),
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def public_version() -> str:
    count = _git("rev-list", "--count", "HEAD")
    return f"{BASE}.{count}" if count else _FALLBACK


def full_version() -> str:
    base = public_version()
    sha = _git("rev-parse", "--short", "HEAD")
    if not sha:
        return base
    dirty = ".dirty" if _git("status", "--porcelain") else ""
    return f"{base}+g{sha}{dirty}"
