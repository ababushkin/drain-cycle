"""Per-issue execution-flow resolution.

The ``verify`` Linear label switches a drain from the default single-worker
flow to the verify pipeline (Task Shaper → Implementer → Outcome Verifier →
PR Preparer → PR Responder). Absence of the label keeps the default flow.
"""
from __future__ import annotations

from typing import Any

VERIFY = "verify"
"""Flow name for the multi-role verify pipeline."""

_VERIFY_LABEL = "verify"


def resolve(issue: dict[str, Any]) -> str | None:
    """Return ``'verify'`` if the issue carries the verify label, else ``None``."""
    if _VERIFY_LABEL in issue.get("labels", []):
        return VERIFY
    return None
