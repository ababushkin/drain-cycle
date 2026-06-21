"""``drain-cycle scorecard``: per-run quality from the run log.

Reads run-log JSON files and reports each run's duration, cost, tokens,
and correctness without any manual confirmation step.

Correctness rule (ADR 0031):
  correct = outcome_verdict.result == "pass" AND review_verdict.result == "GO"
  A missing review verdict is not-correct but is not a violation.
  A Done entry with null outcome_verdict is a silent-Done violation (exit 1).
  prep_verdict.route is advisory — it appears in output but never affects
  the correctness rate or exit code.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def runs_dir() -> Path:
    return Path.home() / ".drain-cycle" / "runs"


def _is_correct(entry: dict[str, Any]) -> bool:
    """True when outcome passed AND review was GO (ADR 0031)."""
    outcome = entry.get("outcome_verdict") or {}
    review = entry.get("review_verdict") or {}
    return (
        outcome.get("result") == "pass"
        and review.get("result") == "GO"
    )


def _is_silent_done(entry: dict[str, Any]) -> bool:
    """True when the entry is Done in Linear but outcome_verdict was never set."""
    return (
        entry.get("final_linear_state") == "Done"
        and entry.get("outcome_verdict") is None
    )


def _prep_route(entry: dict[str, Any]) -> str:
    """Advisory prep-route label; empty string when not present."""
    prep = entry.get("prep_verdict") or {}
    return prep.get("route", "") or ""


def _load_run_files(runs_dir_path: Path) -> list[dict[str, Any]]:
    """Return all valid run-log payloads, sorted by filename (chronological)."""
    if not runs_dir_path.is_dir():
        return []
    payloads = []
    for path in sorted(runs_dir_path.glob("*.json")):
        try:
            payloads.append(json.loads(path.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return payloads


def run(runs_dir_path: Path) -> int:
    """Read run logs, print per-run rows grouped by cycle, and return exit code.

    Exit 0 when no silent-Done violations; exit 1 when any Done entry has
    null outcome_verdict.
    """
    payloads = _load_run_files(runs_dir_path)

    # Group entries by cycle_id, preserving chronological file order.
    cycles: dict[str, list[dict[str, Any]]] = {}
    for payload in payloads:
        cid = payload.get("cycle_id", "<unknown>")
        if cid not in cycles:
            cycles[cid] = []
        cycles[cid].extend(payload.get("entries", []))

    total_runs = 0
    total_correct = 0
    silent_done_issues: list[str] = []

    for cid, entries in cycles.items():
        print(f"\ncycle: {cid}")
        cycle_runs = 0
        cycle_correct = 0

        for entry in entries:
            issue = entry.get("issue_identifier", "?")
            duration = entry.get("duration_seconds")
            cost = entry.get("cost_usd")
            usage = entry.get("usage") or {}
            tokens = usage.get("cumulative")
            correct = _is_correct(entry)
            route = _prep_route(entry)

            duration_str = f"{duration:.1f}s" if duration is not None else "-"
            cost_str = f"${cost:.4f}" if cost is not None else "-"
            tokens_str = str(tokens) if tokens is not None else "-"
            correct_str = "correct" if correct else "not-correct"
            route_str = f"  [{route}]" if route else ""

            print(
                f"  {issue:<12} {duration_str:>8}  {cost_str:>10}  {tokens_str:>8}  "
                f"{correct_str}{route_str}"
            )

            if _is_silent_done(entry):
                silent_done_issues.append(issue)

            cycle_runs += 1
            if correct:
                cycle_correct += 1

        if cycle_runs > 0:
            pct = round(cycle_correct * 100 / cycle_runs)
            print(f"  cycle pass-rate: {cycle_correct}/{cycle_runs} ({pct}%)")
        else:
            print("  cycle pass-rate: n/a")

        total_runs += cycle_runs
        total_correct += cycle_correct

    print()
    if total_runs > 0:
        pct = round(total_correct * 100 / total_runs)
        print(f"overall pass-rate: {total_correct}/{total_runs} ({pct}%)")
    else:
        print("overall pass-rate: n/a")

    if silent_done_issues:
        print("silent-Done violations:")
        for issue in silent_done_issues:
            print(f"  {issue}")
    else:
        print("silent-Done violations: none")

    return 1 if silent_done_issues else 0
