"""Handoff artefact a stack-mode worker leaves behind after submitting PRs.

In stack mode the worker drives PR submission itself via the ``pr-finishing``
skill, which runs ``gt``/``gh`` inside the worktree, posts the Linear
review-summary comment, and records the submitted PRs in ``exec-state.json``
(the pack-named file) as a ``pr_urls`` list. The orchestrator reads that list
back as its confirmation signal — a present, non-empty ``pr_urls`` means the
skill submitted at least one PR; its absence means submission never completed
and the per-repo chain must halt rather than march on.

Schema v2 adds ``outcome_verdict`` and ``prep_verdict`` fields so the worker
can record its self-assessment. The orchestrator reads these on every exit
path — Done, halted, or errored — and lands them in the run-log entry.
``read_partial`` extracts those fields without the ``pr_urls`` validity gate,
so halt paths can carry whatever verdicts the worker managed to write.

``read`` never raises: a missing or malformed file returns ``None`` so callers
can treat it as "not submitted yet."
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXEC_STATE_FILE = "exec-state.json"


@dataclass(frozen=True)
class PullRequest:
    title: str
    url: str


@dataclass(frozen=True)
class HandoffData:
    pr_urls: tuple[PullRequest, ...]
    outcome_verdict: dict[str, Any] | None = None
    prep_verdict: dict[str, Any] | None = None


def _parse_dict(raw: object) -> dict[str, Any] | None:
    """Return ``raw`` only if it is a verdict-shaped dict.

    A verdict is a dict carrying a ``result`` key; downstream code reads
    ``result`` to set span attributes and drive routing. A dict without it is
    not a usable verdict, so it is dropped to ``None`` here rather than passed
    on to crash a reader that assumes the key is present.
    """
    if not isinstance(raw, dict) or "result" not in raw:
        return None
    return raw


def _read_file(path: Path) -> HandoffData | None:
    """Parse a single state file path, returning HandoffData or None.

    A valid file has a ``pr_urls`` list with at least one entry, each entry a
    dict carrying string ``title`` and ``url``. An empty list is treated as
    invalid: the skill writes ``pr_urls`` only after a PR is actually created,
    so "present but empty" means no submission happened.
    """
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("pr_urls")
    if not isinstance(raw, list) or not raw:
        return None
    prs: list[PullRequest] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return None
        title = entry.get("title")
        url = entry.get("url")
        if not isinstance(title, str) or not isinstance(url, str) or not url:
            return None
        prs.append(PullRequest(title=title, url=url))
    return HandoffData(
        pr_urls=tuple(prs),
        outcome_verdict=_parse_dict(payload.get("outcome_verdict")),
        prep_verdict=_parse_dict(payload.get("prep_verdict")),
    )


def read(worktree: Path) -> HandoffData | None:
    """Return the handoff data for ``worktree``, or ``None`` if absent or invalid.

    Reads ``exec-state.json`` (the pack-named file). Returns ``None`` when the
    file is absent or structurally invalid.

    ``outcome_verdict`` and ``prep_verdict`` are optional: missing keys are
    read as ``None`` and do not affect validity.
    """
    return _read_file(worktree / EXEC_STATE_FILE)


def read_partial(
    worktree: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(outcome_verdict, prep_verdict)`` without the ``pr_urls`` gate.

    Used by halt and breach paths to carry any verdicts the worker recorded
    even when ``pr_urls`` is absent or empty and ``read`` would return ``None``.
    Returns ``(None, None)`` when the file is absent or malformed.
    """
    path = worktree / EXEC_STATE_FILE
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    return _parse_dict(payload.get("outcome_verdict")), _parse_dict(payload.get("prep_verdict"))
