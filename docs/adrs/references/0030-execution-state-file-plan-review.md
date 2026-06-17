# Plan review — ADR 0030 + ADR 0002 § Amendment 2026-06-17

**Date:** 2026-06-17
**Issue:** ABA-398
**Tier:** Full (auto-selected by one-way-door attribute: ADR-level contract on shared infrastructure across two repos)
**Cynefin domain:** Complicated

## Verdict

**APPROVE.** Amendment is fully reversible at the document layer; the one-way-door cost lands in the build nodes (ABA-399, ABA-400) that carry their own fixture-test gates. ADR alternatives discipline is satisfied (ADR 0030 enumerates four alternatives — consumer-named handoff, drain-as-container, skills-write-nothing, drop-the-file — and the new write/merge-owner paragraph rejects one-file-per-section). No SUSTAINED verdicts across B1–B8.

## Conditions carried to downstream issues

1. **ABA-399's pickup envelope** must include a first-step grep across `agent-skills-shaper` for `outcome_verdict|prep_verdict|handoff\.write`. If hits exist, the amendment's claim that "no pack-side producer was ever wired" is corrected by a follow-up commit before ABA-399 breakdown. (B3 carry-over.)
2. **ABA-399 + ABA-400** must land paired fixture tests naming the section-key roster on both sides of the seam: the pack lists the keys it writes; the supervisor lists the keys it reads; CI fails on mismatch. (B8 #1 kill-switch.)

## Bucket coverage

- **B1 — Problem framing**: OVERTURNED. Amendment opens with the consumer-coupling problem (inherited from ADR 0030's framing).
- **B2 — Scope clarity**: 4 items reviewed. No SUSTAINED. Two PARTIAL items resolved in-thread (cross-repo follow-up named; orchestrator's `read_partial` call reconciled by "verdict producers were never built").
- **B3 — Assumptions**: 3 assumptions surfaced. "Orchestrator is the sole reader" verified by grep this session (Confidence 8). "No pack-side producer was wired" verified in `drain_cycle/` only (Confidence 7); cross-repo verification deferred to ABA-399. "Per-phase keys are disjoint" — Confidence 5 at amendment-time, OVERTURNED after re-reading the `exec:*` graph this session.
- **B4 — Dependencies**: ABA-399 and ABA-400 explicit; cross-repo pack work owned by Anton.
- **B5 — Reversibility + ADR pairing**: OVERTURNED. Alternatives recorded in ADR 0030 + the amendment's new paragraph. The amendment itself reverts in one commit.
- **B6 — Operability**: Mostly inherited from ADR 0002's existing Operability section; rollback is a single revert. PARTIAL on success metric resolved in-thread by adding the "no module names `.drain-handoff.json`" grep to ADR 0002's Amendment section (commit `10025aa`).
- **B7 — Sequencing + capacity**: Critical path stated (amendment → ABA-399 → ABA-400); appetite fixed at one slice.
- **B8 — Pre-mortem**: Three failure modes named, each with a named kill-switch. The top mode (section-key roster drift) is carried to ABA-399 + ABA-400 as Condition #2 above.

## Files reviewed

- `docs/adrs/0002-thin-supervisor-contract.md` — Amendment 2026-06-17 section + boundary-chart corrections + observable-success line.
- `docs/adrs/0030-execution-state-file.md` — new append-section / write-merge-owner paragraphs.
- `docs/adrs/references/drain-handoff-schema-v2.md` — Superseded-by header.
