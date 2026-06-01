"""Per-issue execution-flow resolution.

A ``verify`` Linear label opts an issue into the verify flow; the resolved
value is recorded in the run log so downstream grading commands can filter
by flow without re-fetching Linear.

* No ``verify`` label → ``None``.
* ``verify`` label present → ``"verify"``.
"""
from __future__ import annotations

from typing import Any

_VERIFY_LABEL = "verify"


def resolve(issue: dict[str, Any]) -> str | None:
    """Return the flow for ``issue``, or ``None`` if no flow label is set."""
    if _VERIFY_LABEL in issue.get("labels", []):
        return _VERIFY_LABEL
    return None
