# ADR 0031: The scorecard correctness contract — outcome pass AND review GO

**Date:** 2026-06-18
**Status:** Accepted
**Plan-review:** APPROVE (Full tier, 2026-06-18) — condition: name the `grade` deprecation owner (addressed in Consequences §1).

The scorecard project replaces the manual `grade` command with an automated `drain-cycle scorecard` that reports duration, cost, and correctness across a cycle's runs ([`architecture.html`](../architecture.html) §7 supervisor-as-process). Before the rule is built, its semantics need to be fixed — otherwise the automation re-encodes today's bug, where a confirmed Done with `outcome_verdict.result == "fail"` is counted as a pass because `grade.py` checks only that the verdict exists, not what it says (`drain_cycle/grade.py:72-77`).

**Decision.** A run is **correct** when both of the following hold in the run-log entry: `outcome_verdict.result == "pass"` **AND** `review_verdict.result == "GO"`. The manual draft/confirmed grade-file gate is removed — there is no human approval step between the run-log entry and the scorecard. `prep_verdict.route` is treated as advisory and deferred from the correctness rule until a producer exists; today no skill emits it, so making it a gate would block every run on a cross-repo dependency that has not been built. Silent-Done remains the hard violation, unchanged: a run-log entry with `final_linear_state == "Done"` and `outcome_verdict == null` fails the scorecard regardless of any other field. `review_verdict` is added to the run-log entry as a new additive field; absence is tolerated until producers backfill, but presence with `result == "NO-GO"` flips the run from correct to incorrect.

**Why the AND, not just outcome.** Outcome-only correctness is the agent grading its own work: the same execution that wrote the diff also produced the outcome verdict. Review is the independent persona pass over the same diff (ADR 0025) — an outcome-pass plus a review-GO is two artefacts, written by different invocations, agreeing. That is the cheapest available second opinion without adding a human.

**Why prep_verdict.route is advisory, not gating.** `prep_verdict` is shaped to carry the upstream prep skill's verdict including a `route` field (`auto-merge` / `human-review`). No skill writes it today. A correctness rule that reads `prep_verdict.route` would either block every run (when the field is `null`) or silently degrade to outcome+review (when the field is present but ignored). Advisory-and-deferred is the honest stance: the field is recorded if produced, the scorecard reports it, the correctness rule does not consult it. When a producer ships, a follow-up ADR can promote the field into the rule.

**Why silent-Done stays the hard violation.** The whole point of the run-log entry is that a Linear Done has a verifiable shadow record (ADR 0008, ADR 0022). A Done with no `outcome_verdict` is a run that completed without verification — the failure mode `grade.py` was built to catch (`drain_cycle/grade.py:72-77`). Folding it into the AND rule would let a missing outcome read as "outcome did not pass" and become indistinguishable from "outcome ran and failed." Keeping it a separate, hard violation preserves that signal.

**Run-log shape after this decision.** Each entry gains one new key:

```text
review_verdict: null | {"result": "GO"|"NO-GO", "findings": [...], "invocation_id": "..."}
```

Producers (the review skill, today `exec:review`) write it when they run; the orchestrator passes it through. The schema change is additive — entries written before producers exist read `null`, and the scorecard treats `null` as "review did not run" (advisory, not violating) rather than as NO-GO. A v2 schema bump is not required.

**Scope of "correctness" in the scorecard.** The scorecard surfaces three counts per cycle: **correct** (outcome pass AND review GO), **incorrect-with-verdict** (outcome fail OR review NO-GO), and **silent-Done** (Done with `outcome_verdict == null`). Runs with `review_verdict == null` but `outcome_verdict.result == "pass"` are reported as **outcome-only-pass** in a separate row so the rate is not silently inflated. Per-run granularity stays in the run-log; per-cycle aggregates are the scorecard's job.

**Alternatives considered.**

- *Outcome-only correctness.* Rejected: re-encodes today's bug — the agent that wrote the diff is the only one judging it. No independent signal in the rule.
- *Outcome AND review AND prep-route.* Rejected for now: no producer of `prep_verdict.route` exists; the gate would block every run on a cross-repo dependency. Promote when the producer ships.
- *Keep the manual draft/confirmed grade gate.* Rejected: the gate's value is signing that a human reviewed the run, but the human signal is already captured by the merged PR. Two gates on the same axis halves throughput without adding evidence.
- *Treat fail as silent-Done.* Rejected: collapses two distinct signals — "ran and produced a fail verdict" vs "never produced a verdict" — into one row. The first is the system working; the second is the system silently regressing. Keeping them separate preserves the failure-mode distinction `grade.py` was built around.
- *Bump run-log schema to v2 for `review_verdict`.* Rejected: the field is additive — `null` is a defined value, no consumer breaks. v2 is reserved for breaking changes.

**Consequences.**

- The manual `drain-cycle grade` command and its draft/confirmed file workflow are deprecated; the scorecard reads run-log entries directly. Removal of the `grade` command is tracked as the cycle's ktlo node (N05); until then the two coexist and the scorecard is the source of truth.
- `exec:review` is the producer of `review_verdict`; the orchestrator records it in the run-log entry alongside `outcome_verdict` and `prep_verdict`.
- The scorecard's correctness rule has one place to read — the run-log entry — and one assertion to make: outcome pass AND review GO. Both gates are independently invoked, neither writes the other's field.
- A run with no review verdict is visible in the report (outcome-only-pass row), not silently counted as correct.
- When a `prep_verdict.route` producer ships, promoting it into the rule is a follow-up ADR plus a single conjunct in the assertion — no schema migration.
