"""Handoff artefact a stack-mode worker leaves behind after submitting PRs.

In stack mode the worker drives PR submission itself via the ``pr-finishing``
skill, which runs ``gt``/``gh`` inside the worktree, posts the Linear
review-summary comment, and records the submitted PRs in ``exec-state.json``
(the pack-named file). The pack writes that file as a set of phase-keyed
sections — ``pickup``, ``breakdown``, ``build``, ``review``, ``verify``,
``finish`` — each skill owning its own section (ADR 0030). The orchestrator
reads two of those sections back: ``finish.pr_urls`` as its submission-confirmation
signal — a present, non-empty list means the skill submitted at least one PR;
its absence means submission never completed and the per-repo chain must halt —
and the ``verify`` section, which it maps to its ``outcome_verdict`` for the
run-log and the verifier-fail gate.

``read`` reads the sectioned ``exec-state.json`` — the only state file. The
legacy flat ``.drain-handoff.json`` has been dropped. A missing or structurally
invalid file returns ``None``.

``outcome_verdict`` is derived from the pack's ``verify`` section: a ``FAIL``
verdict maps to ``result == "fail"`` with the failed AC items as ``findings``.
``read_partial`` extracts that verdict without the ``pr_urls`` validity gate, so
halt paths can carry whatever the worker managed to write. ``prep_verdict`` has
no producer in the ``exec:*`` workflow today, so it is always ``None``.

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
    review_verdict: dict[str, Any] | None = None


def _load(path: Path) -> dict[str, Any] | None:
    """Parse ``path`` as a JSON object, or ``None`` if absent/malformed."""
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


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


def _parse_pr_urls(raw: object) -> tuple[PullRequest, ...] | None:
    """Validate a ``pr_urls`` list, returning the parsed PRs or ``None``.

    A valid list has at least one entry, each a dict carrying a string
    ``title`` and a non-empty string ``url``. An empty list is invalid: the
    skill writes ``pr_urls`` only after a PR is actually created, so "present
    but empty" means no submission happened.
    """
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
    return tuple(prs)


def _verify_to_outcome(raw: object) -> dict[str, Any] | None:
    """Map the pack's ``verify`` section to the supervisor's outcome verdict.

    The pack records ``{"verdict": "PASS"|"FAIL", "ac_results": [{"item",
    "result"}]}``. Downstream the supervisor reads ``result`` ("pass"/"fail")
    and ``findings``, so a ``FAIL`` verdict becomes ``result == "fail"`` with
    the failing AC items as ``findings``. A section without a recognised
    verdict is dropped to ``None``.
    """
    if not isinstance(raw, dict):
        return None
    verdict = raw.get("verdict")
    if verdict not in ("PASS", "FAIL"):
        return None
    ac_results = raw.get("ac_results")
    findings: list[str] = []
    if isinstance(ac_results, list):
        for ac in ac_results:
            if isinstance(ac, dict) and ac.get("result") == "FAIL":
                item = ac.get("item")
                if isinstance(item, str):
                    findings.append(item)
    return {
        "result": "fail" if verdict == "FAIL" else "pass",
        "findings": findings,
    }


def _review_to_verdict(raw: object) -> dict[str, Any] | None:
    """Map the pack's ``review`` section to the supervisor's review verdict.

    The pack records ``{"verdict": "GO"|"NO-GO", "findings": [...]}``.
    Downstream the supervisor reads ``result`` ("go"/"no-go") and ``findings``.
    A section without a recognised verdict is dropped to ``None``.
    """
    if not isinstance(raw, dict):
        return None
    verdict = raw.get("verdict")
    if verdict not in ("GO", "NO-GO"):
        return None
    findings = raw.get("findings")
    safe_findings: list[str] = []
    if isinstance(findings, list):
        safe_findings = [f for f in findings if isinstance(f, str)]
    return {
        "result": verdict.lower(),
        "findings": safe_findings,
    }


def _read_exec_state(path: Path) -> HandoffData | None:
    """Parse the sectioned ``exec-state.json``, returning HandoffData or None.

    ``pr_urls`` comes from the ``finish`` section; ``outcome_verdict`` from the
    ``verify`` section. Returns ``None`` unless a valid, non-empty
    ``finish.pr_urls`` is present — that list is the submission signal.
    """
    payload = _load(path)
    if payload is None:
        return None
    finish = payload.get("finish")
    raw_urls = finish.get("pr_urls") if isinstance(finish, dict) else None
    prs = _parse_pr_urls(raw_urls)
    if prs is None:
        return None
    return HandoffData(
        pr_urls=prs,
        outcome_verdict=_verify_to_outcome(payload.get("verify")),
        prep_verdict=None,
        review_verdict=_review_to_verdict(payload.get("review")),
    )


def read(worktree: Path) -> HandoffData | None:
    """Return the handoff data for ``worktree``, or ``None`` if absent or invalid.

    Reads the sectioned ``exec-state.json`` (the pack-named file). Returns
    ``None`` when the file is absent or structurally invalid.

    ``outcome_verdict`` and ``prep_verdict`` are optional: missing sections/keys
    are read as ``None`` and do not affect validity.
    """
    return _read_exec_state(worktree / EXEC_STATE_FILE)


def read_partial(
    worktree: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(outcome_verdict, prep_verdict, review_verdict)`` without the ``pr_urls`` gate.

    Used by halt and breach paths to carry any verdicts the worker recorded
    even when ``pr_urls`` is absent or empty and ``read`` would return ``None``.
    Reads the ``verify`` section for ``outcome_verdict`` and the ``review``
    section for ``review_verdict``; there is no ``prep_verdict`` producer in
    the ``exec:*`` workflow today, so it is always ``None``. Returns
    ``(None, None, None)`` when the file is absent or malformed.
    """
    exec_state = _load(worktree / EXEC_STATE_FILE)
    if exec_state is not None:
        return (
            _verify_to_outcome(exec_state.get("verify")),
            None,
            _review_to_verdict(exec_state.get("review")),
        )
    return None, None, None
