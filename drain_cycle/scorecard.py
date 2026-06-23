"""``drain-cycle scorecard``: per-run quality from the run log.

Reads run-log JSON files and renders a dashboard: a headline pass-rate with a
per-cycle trend sparkline, then a Rich table per cycle showing each run's
duration, cost, tokens, and a status glyph — no manual confirmation step.

Correctness rule (ADR 0031):
  correct = outcome_verdict.result == "pass" AND review_verdict.result == "go"
  A missing review verdict is not-correct but is not a violation.
  A Done entry with null outcome_verdict is a silent-Done violation (exit 1).
  prep_verdict.route is advisory — it appears in output but never affects
  the correctness rate or exit code.

The output goes to stdout (it is a report, meant to be read and piped). Rich
strips color in a non-TTY, so the sparkline and status glyphs still read as
plain text when piped or captured.
"""
from __future__ import annotations

import json
import sys
from enum import Enum
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from .progress import fmt_tokens

_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


class Status(str, Enum):
    """A run's three-way render state."""

    CORRECT = "correct"
    FAILED = "failed"
    UNSCORED = "unscored"


def runs_dir() -> Path:
    return Path.home() / ".drain-cycle" / "runs"


def _is_correct(entry: dict[str, Any]) -> bool:
    """True when outcome passed AND review was GO (ADR 0031)."""
    outcome = entry.get("outcome_verdict") or {}
    review = entry.get("review_verdict") or {}
    return (
        outcome.get("result") == "pass"
        and review.get("result") == "go"
    )


def _is_silent_done(entry: dict[str, Any]) -> bool:
    """True when the entry is Done in Linear but outcome_verdict was never set."""
    return (
        entry.get("final_linear_state") == "Done"
        and entry.get("outcome_verdict") is None
    )


def _status(entry: dict[str, Any]) -> Status:
    """Map an entry to CORRECT, FAILED, or UNSCORED.

    UNSCORED: neither outcome_verdict nor review_verdict is present — the run
    was never evaluated. CORRECT: both present and the correctness rule passes.
    FAILED: at least one verdict present but the correctness rule fails.
    """
    outcome = entry.get("outcome_verdict")
    review = entry.get("review_verdict")
    if outcome is None and review is None:
        return Status.UNSCORED
    if _is_correct(entry):
        return Status.CORRECT
    return Status.FAILED


def _status_glyph(status: Status) -> str:
    """Rich-markup glyph for a status; color is stripped in a non-TTY."""
    if status is Status.CORRECT:
        return "[green]✓[/green]"
    if status is Status.UNSCORED:
        return "[dim]—[/dim]"
    return "[red]✗[/red]"


def _prep_route(entry: dict[str, Any]) -> str:
    """Advisory prep-route label; empty string when not present."""
    prep = entry.get("prep_verdict") or {}
    return prep.get("route", "") or ""


def _sparkline(values: list[float]) -> str:
    """Render fractions in [0.0, 1.0] as unicode block characters."""
    if not values:
        return ""
    top = len(_SPARK_BLOCKS) - 1
    return "".join(
        _SPARK_BLOCKS[min(top, max(0, round(v * top)))] for v in values
    )


def _latest_by_issue(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one entry per issue_identifier — the last occurrence in the list."""
    seen: dict[str, dict[str, Any]] = {}
    for entry in entries:
        issue = entry.get("issue_identifier") or "?"
        seen[issue] = entry
    return list(seen.values())


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


def _render(
    console: Console, cycles: dict[str, list[dict[str, Any]]]
) -> tuple[int, int, list[str]]:
    """Render the dashboard and return (total_runs, total_correct, silent_done)."""
    console.rule("[bold]drain-cycle scorecard[/bold]")

    total_runs = 0  # scored only; unscored excluded from pass-rate denominator
    total_correct = 0
    total_unscored = 0
    total_cost = 0.0
    silent_done_issues: list[str] = []
    cycle_rates: list[float] = []  # chronological, for the headline sparkline
    per_cycle: list[tuple[str, list[dict[str, Any]], int, int, float, int]] = []

    for cid, entries in cycles.items():
        runs = 0
        correct = 0
        cycle_unscored = 0
        # Cost and silent-done accumulate across all attempts.
        for entry in entries:
            cost = entry.get("cost_usd")
            if cost is not None:
                total_cost += cost
            if _is_silent_done(entry):
                silent_done_issues.append(entry.get("issue_identifier", "?"))
        # Correctness counts once per issue, using the latest attempt.
        # UNSCORED runs are excluded from the denominator: they have no verdict
        # and cannot be judged, so they don't drag down the pass rate.
        for entry in _latest_by_issue(entries):
            status = _status(entry)
            if status is Status.UNSCORED:
                cycle_unscored += 1
                total_unscored += 1
                continue
            runs += 1
            if status is Status.CORRECT:
                correct += 1
        rate = correct / runs if runs else 0.0
        per_cycle.append((cid, entries, runs, correct, rate, cycle_unscored))
        if runs:
            cycle_rates.append(rate)
        total_runs += runs
        total_correct += correct

    # Headline KPI block.
    if total_runs:
        pct = round(total_correct * 100 / total_runs)
        rate_str = f"{total_correct}/{total_runs} ({pct}%)"
    else:
        rate_str = "n/a"
    spark = _sparkline(cycle_rates)
    spark_str = f"   {spark}" if spark else ""
    console.print(f"  [bold]Pass rate[/bold]  {rate_str}{spark_str}")
    console.print(
        f"  {total_runs} scored · {total_unscored} unscored · "
        f"{len(silent_done_issues)} violations · ${total_cost:.2f}"
    )

    # Per-cycle detail.
    for cid, entries, runs, correct, rate, cycle_unscored in per_cycle:
        if runs:
            cpct = round(rate * 100)
            rate_label = f"{correct}/{runs} ({cpct}%)"
        else:
            rate_label = "n/a"
        unscored_str = f"  {cycle_unscored} unscored" if cycle_unscored else ""
        console.print(
            f"\n[bold]Cycle {cid}[/bold]   {rate_label}  "
            f"{_sparkline([rate])}{unscored_str}"
        )
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("issue", no_wrap=True)
        table.add_column("duration", justify="right")
        table.add_column("cost", justify="right")
        table.add_column("tokens", justify="right")
        table.add_column("status")
        for entry in entries:
            issue = entry.get("issue_identifier", "?")
            duration = entry.get("duration_seconds")
            cost = entry.get("cost_usd")
            usage = entry.get("usage") or {}
            tokens = usage.get("cumulative")

            duration_str = f"{duration:.1f}s" if duration is not None else "-"
            cost_str = f"${cost:.4f}" if cost is not None else "-"
            tokens_str = fmt_tokens(tokens) if tokens is not None else "-"
            route = _prep_route(entry)
            status_cell = _status_glyph(_status(entry))
            if route:
                status_cell = f"{status_cell} {route}"

            table.add_row(issue, duration_str, cost_str, tokens_str, status_cell)
        console.print(table)

    # Silent-Done violations.
    console.print()
    if silent_done_issues:
        console.print("silent-Done violations:")
        for issue in silent_done_issues:
            console.print(f"  {issue}")
    else:
        console.print("silent-Done violations: none")

    return total_runs, total_correct, silent_done_issues


def run(runs_dir_path: Path) -> int:
    """Read run logs, render the dashboard, and return an exit code.

    Exit 0 when no silent-Done violations; exit 1 when any Done entry has
    null outcome_verdict.
    """
    # Built lazily against the live sys.stdout so pytest's capsys capture works.
    console = Console(file=sys.stdout, highlight=False, soft_wrap=True)

    payloads = _load_run_files(runs_dir_path)

    # Group entries by cycle_id, preserving chronological file order.
    cycles: dict[str, list[dict[str, Any]]] = {}
    for payload in payloads:
        cid = payload.get("cycle_id", "<unknown>")
        if cid not in cycles:
            cycles[cid] = []
        cycles[cid].extend(payload.get("entries", []))

    _, _, silent_done_issues = _render(console, cycles)

    return 1 if silent_done_issues else 0
