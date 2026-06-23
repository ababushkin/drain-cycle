"""``drain-cycle scorecard``: aggregate run quality from the run log.

Reads run-log JSON files and renders a dashboard built for monitoring the
product across many cycles: a SUMMARY block of rolled-up statistics (pass rate,
fully-executed rate, retry rate, average cost and duration, cost per correct
issue), then one summary row per cycle. The per-issue rows are hidden by
default and shown with ``--detail``.

Correctness rule (ADR 0031):
  correct = outcome_verdict.result == "pass" AND review_verdict.result == "go"
  A missing review verdict is not-correct but is not a violation.
  An instrumented Done entry with null outcome_verdict is a silent-Done
  violation (exit 1); a Done entry that omits the field predates verdict
  capture and is not judged.
  prep_verdict.route is advisory — it appears in the detail rows but never
  affects the correctness rate or exit code.

Fully executed = the latest attempt finished cleanly (exit_code 0 and no
halt_reason). It measures pipeline reliability, separate from correctness: a
run can execute fully and still fail review.

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

from .progress import fmt_elapsed, fmt_tokens

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
    """True when an instrumented entry is Done in Linear but carries no outcome verdict.

    Only entries that use the verdict-carrying schema are judged: the
    ``outcome_verdict`` key is present (a null value is the violation). Entries
    that omit the key predate verdict capture and cannot be held to the gate.
    """
    return (
        "outcome_verdict" in entry
        and entry.get("final_linear_state") == "Done"
        and entry.get("outcome_verdict") is None
    )


def _is_executed(entry: dict[str, Any]) -> bool:
    """True when the run finished cleanly: exit_code 0 and no halt_reason.

    Reliability signal, separate from correctness — a fully-executed run can
    still fail review.
    """
    return entry.get("exit_code") == 0 and entry.get("halt_reason") is None


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


def _fmt_rate(correct: int, total: int) -> str:
    """Render ``correct/total (pct%)`` — or ``n/a`` when total is zero."""
    if not total:
        return "n/a"
    pct = round(correct * 100 / total)
    return f"{correct}/{total} ({pct}%)"


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


def _group_cycles(payloads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group entries by cycle_id, preserving chronological file order.

    Retains each payload's ``cycle_duration_seconds`` (the per-invocation
    wall-clock), summed across files that share a cycle_id. ``has_duration``
    records whether any payload supplied it, so the aggregate can fall back to
    summing per-entry durations for legacy logs that omit the field.
    """
    cycles: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        cid = payload.get("cycle_id", "<unknown>")
        cyc = cycles.setdefault(cid, {"entries": [], "duration": 0.0, "has_duration": False})
        cyc["entries"].extend(payload.get("entries", []))
        cycle_duration = payload.get("cycle_duration_seconds")
        if cycle_duration is not None:
            cyc["duration"] += cycle_duration
            cyc["has_duration"] = True
    return cycles


def _cycle_duration(cyc: dict[str, Any]) -> float:
    """Wall-clock for a cycle: the payload value, or the sum of entry durations."""
    if cyc["has_duration"]:
        return cyc["duration"]
    return sum(e.get("duration_seconds") or 0.0 for e in cyc["entries"])


def _aggregate(
    cycles: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Roll grouped cycles up into overall and per-cycle statistics.

    Cost and tokens accumulate across all attempts; correctness, executed, and
    unscored counts use one entry per issue (the latest attempt), matching the
    pass-rate denominator. An issue with more than one entry in its cycle is
    counted once as retried.
    """
    per_cycle: list[dict[str, Any]] = []
    silent_done: list[str] = []

    for cid, cyc in cycles.items():
        entries = cyc["entries"]

        attempts: dict[str, int] = {}
        cost = 0.0
        tokens = 0
        for entry in entries:
            issue = entry.get("issue_identifier") or "?"
            attempts[issue] = attempts.get(issue, 0) + 1
            entry_cost = entry.get("cost_usd")
            if entry_cost is not None:
                cost += entry_cost
            cumulative = (entry.get("usage") or {}).get("cumulative")
            if cumulative is not None:
                tokens += cumulative
            if _is_silent_done(entry):
                silent_done.append(entry.get("issue_identifier", "?"))
        retried = sum(1 for n in attempts.values() if n > 1)

        latest = _latest_by_issue(entries)
        runs = correct = unscored = executed = 0
        for entry in latest:
            if _is_executed(entry):
                executed += 1
            status = _status(entry)
            if status is Status.UNSCORED:
                unscored += 1
                continue
            runs += 1
            if status is Status.CORRECT:
                correct += 1

        per_cycle.append(
            {
                "cid": cid,
                "entries": entries,
                "issues": len(latest),
                "executed": executed,
                "runs": runs,
                "correct": correct,
                "rate": correct / runs if runs else 0.0,
                "unscored": unscored,
                "retried": retried,
                "cost": cost,
                "duration": _cycle_duration(cyc),
                "tokens": tokens,
            }
        )

    overall = {
        "cycles": len(per_cycle),
        "issues": sum(c["issues"] for c in per_cycle),
        "runs": sum(c["runs"] for c in per_cycle),
        "correct": sum(c["correct"] for c in per_cycle),
        "unscored": sum(c["unscored"] for c in per_cycle),
        "executed": sum(c["executed"] for c in per_cycle),
        "retried": sum(c["retried"] for c in per_cycle),
        "cost": sum(c["cost"] for c in per_cycle),
        "duration": sum(c["duration"] for c in per_cycle),
        "tokens": sum(c["tokens"] for c in per_cycle),
        "rates": [c["rate"] for c in per_cycle if c["runs"]],
        "silent_done": silent_done,
    }
    return overall, per_cycle


def _render_summary(console: Console, overall: dict[str, Any]) -> None:
    """Print the rolled-up SUMMARY block."""
    cycles = overall["cycles"]
    issues = overall["issues"]
    cost = overall["cost"]
    duration = overall["duration"]

    console.print(f"[bold]SUMMARY[/bold]  ({cycles} cycles · {issues} issues)")

    spark = _sparkline(overall["rates"])
    spark_str = f"   {spark}" if spark else ""
    console.print(
        f"  Pass rate        {_fmt_rate(overall['correct'], overall['runs'])}{spark_str}"
    )
    console.print(
        f"  Fully executed   {_fmt_rate(overall['executed'], issues)}"
    )
    console.print(
        f"  Retry rate       {_fmt_rate(overall['retried'], issues)}"
    )
    cost_per_cycle = f"${cost / cycles:.2f}" if cycles else "n/a"
    cost_per_issue = f"${cost / issues:.2f}" if issues else "n/a"
    console.print(
        f"  Avg cost         {cost_per_cycle} / cycle   ·   {cost_per_issue} / issue"
    )
    dur_per_cycle = fmt_elapsed(duration / cycles) if cycles else "n/a"
    dur_per_issue = fmt_elapsed(duration / issues) if issues else "n/a"
    console.print(
        f"  Avg duration     {dur_per_cycle} / cycle   ·   {dur_per_issue} / issue"
    )
    correct = overall["correct"]
    cost_per_correct = f"${cost / correct:.2f}" if correct else "n/a"
    console.print(f"  Cost / correct   {cost_per_correct}")
    console.print(
        f"  Health           {overall['runs']} scored · {overall['unscored']} unscored · "
        f"{len(overall['silent_done'])} violations · ${cost:.2f} total"
    )


def _render_cycle_table(console: Console, per_cycle: list[dict[str, Any]]) -> None:
    """Print one summary row per cycle."""
    if not per_cycle:
        return
    console.print("\n[bold]PER-CYCLE[/bold]")
    table = Table(box=None, pad_edge=False)
    table.add_column("cycle", no_wrap=True)
    table.add_column("issues", justify="right")
    table.add_column("executed", justify="right")
    table.add_column("pass")
    table.add_column("cost", justify="right")
    table.add_column("duration", justify="right")
    table.add_column("tokens", justify="right")
    for c in per_cycle:
        table.add_row(
            c["cid"][:8],
            str(c["issues"]),
            f"{c['executed']}/{c['issues']}",
            _fmt_rate(c["correct"], c["runs"]),
            f"${c['cost']:.2f}",
            fmt_elapsed(c["duration"]),
            fmt_tokens(c["tokens"]),
        )
    console.print(table)


def _render_detail(console: Console, per_cycle: list[dict[str, Any]]) -> None:
    """Print the per-issue rows beneath each cycle (``--detail``)."""
    for c in per_cycle:
        rate_label = _fmt_rate(c["correct"], c["runs"])
        unscored_str = f"  {c['unscored']} unscored" if c["unscored"] else ""
        console.print(
            f"\n[bold]Cycle {c['cid']}[/bold]   {rate_label}  "
            f"{_sparkline([c['rate']])}{unscored_str}"
        )
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column("issue", no_wrap=True)
        table.add_column("duration", justify="right")
        table.add_column("cost", justify="right")
        table.add_column("tokens", justify="right")
        table.add_column("status")
        for entry in c["entries"]:
            issue = entry.get("issue_identifier", "?")
            duration = entry.get("duration_seconds")
            cost = entry.get("cost_usd")
            tokens = (entry.get("usage") or {}).get("cumulative")

            duration_str = f"{duration:.1f}s" if duration is not None else "-"
            cost_str = f"${cost:.4f}" if cost is not None else "-"
            tokens_str = fmt_tokens(tokens) if tokens is not None else "-"
            route = _prep_route(entry)
            status_cell = _status_glyph(_status(entry))
            if route:
                status_cell = f"{status_cell} {route}"

            table.add_row(issue, duration_str, cost_str, tokens_str, status_cell)
        console.print(table)


def _render(
    console: Console, cycles: dict[str, dict[str, Any]], detail: bool
) -> list[str]:
    """Render the dashboard and return the silent-Done issue list."""
    console.rule("[bold]drain-cycle scorecard[/bold]")

    overall, per_cycle = _aggregate(cycles)
    _render_summary(console, overall)
    _render_cycle_table(console, per_cycle)
    if detail:
        _render_detail(console, per_cycle)

    console.print()
    silent_done = overall["silent_done"]
    if silent_done:
        console.print("silent-Done violations:")
        for issue in silent_done:
            console.print(f"  {issue}")
    else:
        console.print("silent-Done violations: none")

    return silent_done


def run(runs_dir_path: Path, detail: bool = False) -> int:
    """Read run logs, render the dashboard, and return an exit code.

    Exit 0 when no silent-Done violations; exit 1 when any Done entry has
    null outcome_verdict. ``detail`` adds the per-issue rows under each cycle.
    """
    # Built lazily against the live sys.stdout so pytest's capsys capture works.
    console = Console(file=sys.stdout, highlight=False, soft_wrap=True)

    payloads = _load_run_files(runs_dir_path)
    cycles = _group_cycles(payloads)
    silent_done = _render(console, cycles, detail)

    return 1 if silent_done else 0
