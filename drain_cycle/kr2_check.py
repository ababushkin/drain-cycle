"""KR2 schema check: every Done run-log entry must carry non-null verdicts.

A Done entry without ``outcome_verdict`` or ``prep_verdict`` is uninspectable
— the operator cannot grade the cycle. Null verdicts on non-Done entries are
fine: a halt or error explains the missing assessment via ``halt_reason``.

Usage::

    python -m drain_cycle.kr2_check <run-log.json> [...]

Exits 0 when all Done entries in every file carry both verdicts.
Exits 1 when any Done entry lacks either verdict.
Exits 2 on bad invocation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def check_file(path: Path) -> list[str]:
    """Return a violation message for each Done entry missing a verdict."""
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{path}: unreadable — {exc}"]
    violations: list[str] = []
    for entry in payload.get("entries", []):
        if entry.get("final_linear_state") != "Done":
            continue
        ident = entry.get("issue_identifier", "<unknown>")
        if entry.get("outcome_verdict") is None:
            violations.append(f"{path}: Done entry {ident} missing outcome_verdict")
        if entry.get("prep_verdict") is None:
            violations.append(f"{path}: Done entry {ident} missing prep_verdict")
    return violations


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        print("usage: kr2_check <run-log.json> [...]", file=sys.stderr)
        return 2
    violations: list[str] = []
    for arg in argv:
        violations.extend(check_file(Path(arg)))
    for v in violations:
        print(v, file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
