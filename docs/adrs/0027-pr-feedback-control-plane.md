# ADR 0027: Responding to PR review feedback is a control-plane behaviour, not a pack skill

**Date:** 2026-06-16
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §23

An earlier plan framed PR-feedback response as a Layer-2 pack skill (`/shape:pr-respond`, ABA-331) driven by an operator-launched foreground polling loop (`drain-cycle pr-feedback`, ABA-332). Both are cancelled. Responding to review comments is a **Layer-1 control-plane behaviour** of the resident daemon ([architecture.html](../architecture.html) §9–§10), because it requires watching a PR *after it is open* — a liveness only a resident process has. A pack skill runs inside one worker session bounded to a single diff; it cannot watch a PR across review rounds. The responsibility belongs to the daemon, not the pack.

**The idempotency contract (carried forward from the cancelled ABA-331).** The daemon must never address the same review comment twice across invocations. The run-log already carries the write target: `responder_runs[]`, an array of `{comment_ids[], invoked_at, result}` objects (shipped null/empty by ABA-321). "New" means a comment ID absent from every prior `responder_runs[].comment_ids[]` for the issue. A poll that finds no new comments exits cleanly without changes; running twice over the same comment produces neither a duplicate fix nor a duplicate log entry.

**Mechanism (deferred to Gear 3).** The concrete loop — read new comments → revise in the issue's worktree → re-submit to the stack → append to `responder_runs[]` — is the Gear 3 PR-review lifecycle in [`ideas/drain-past-the-merge-gate.md`](../ideas/drain-past-the-merge-gate.md), gated behind the merge-gate evolution. This decision records *where the responsibility lives and why*; the gear doc owns the *how* and its open questions.

**Why this matters now.** PR-feedback response is part of the supervisor's autonomy horizon ([architecture.html](../architecture.html) §9): of the horizon behaviours, the merge-gate evolution is shaped but the feedback loop had only the two cancelled tickets to carry it. Recording the relocation keeps the behaviour — and the idempotency design it had already worked out — from being silently dropped when ABA-331/332 closed.

**Alternatives considered.**

- *Pack skill `/shape:pr-respond` + `drain-cycle pr-feedback` polling subcommand (the original plan).* Rejected: a skill is bounded to one worker session and one diff, so it cannot watch a PR after it is open; a foreground operator poller is a hand-cranked stand-in for the daemon's own liveness. Superseded by control-plane ownership.
- *Resolve wrong-fix escalation here.* Deferred: if the daemon's revision for a comment is itself wrong, the operator has no automated escalation path. Left as a Gear 3 open question rather than decided now.
