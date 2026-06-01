"""Handoff artefact written by a stack-mode agent for the assembler.

The spawned agent writes ``.drain-handoff.json`` at the worktree root before
finishing so the orchestrator's assembly step can turn it into a Graphite PR
without re-reading the agent's output. ``read`` never raises: a missing or
malformed file returns ``None`` so callers can treat it as "not ready yet."
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

HANDOFF_FILE = ".drain-handoff.json"


@dataclass(frozen=True)
class HandoffData:
    pr_title: str
    pr_body: str
    findings: dict[str, int]


def write(worktree: Path, data: HandoffData) -> None:
    """Serialise ``data`` to ``<worktree>/.drain-handoff.json``."""
    path = worktree / HANDOFF_FILE
    path.write_text(
        json.dumps(
            {
                "pr_title": data.pr_title,
                "pr_body": data.pr_body,
                "findings": data.findings,
            },
            indent=2,
        )
    )


def read(worktree: Path) -> HandoffData | None:
    """Return the handoff data for ``worktree``, or ``None`` if absent or invalid."""
    path = worktree / HANDOFF_FILE
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    pr_title = payload.get("pr_title")
    pr_body = payload.get("pr_body")
    findings = payload.get("findings")
    if not isinstance(pr_title, str) or not isinstance(pr_body, str):
        return None
    if not isinstance(findings, dict):
        return None
    return HandoffData(pr_title=pr_title, pr_body=pr_body, findings=findings)
