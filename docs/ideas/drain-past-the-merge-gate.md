# Drain past the merge gate

> **Layer 1, gated behind the keystone.** This is pure supervision — the orchestrator's autonomy horizon — under the two-layer architecture in [`docs/architecture.html`](../architecture.html). It does not start until the keystone lands: `prompt.py` stripped to a pointer at `exec:pickup`, the cutover recorded in [ADR 0028](../adrs/0028-keystone-cutover.md). Read the [vision](../vision.md) first.

## Problem Statement
How might we let a drain run keep doing useful work — and keep turning hands-free — when the only thing left blocking it is a human review-and-merge gate on PRs it already opened?

## Context
drain-cycle is transactional per issue: a worker marks a Linear issue **Done**, the orchestrator stacks its PR via Graphite, and continues. Within a run, downstream work stacks fine. The pain appears when the drain runs out of *runnable* work: anything still blocked by an **unmerged** dependency gets **deferred** (`linear.py`, external-blocker defer — `state_type not in _RESOLVED_STATE_TYPES`). The run ends, and the cycle lingers until the owner reviews + merges. The human merge serializes everything downstream.

Desired end-state (from session): build on unmerged work, auto-merge *trusted* classes, and a loop that resumes as merges land — minimal owner touch points. Default trust posture is conservative (gate unless told otherwise).

## Recommended Direction
A **three-gear** evolution, each independently shippable, smallest-blast-radius first. **The destination is a real resident daemon** — a drain-cycle process that stays alive, watches its own PRs, and will eventually host a full PR-review lifecycle (request review → ingest feedback → revise → re-submit → merge) as a long-running loop. The gears below are ordered so each one ships value on its own *and* lays a brick toward that daemon; a cron/`/loop` relaunch is an acceptable **interim** host for Gears 1–2 while the resident process is built, not the end state.

**Gear 1 — Optimistic stacking (within-run).**

*The problem in one picture.* Say the cycle has A → B → C, where B is blocked-by A and C is blocked-by B. Today:
- Worker finishes A, marks it Done, opens PR #1 (branch `aba-A`, based on `main`).
- Worker finishes B *only because* A is Done in Linear — B's branch `aba-B` stacks on `aba-A`, PR #2 opens.
- This already works **inside one run**. The trouble is the boundary: the moment a downstream issue is blocked by something whose PR is open-but-**unmerged** and that the run can't reach by stacking (e.g. it was deferred, in a later cycle, or a different repo), `linear.py`'s defer rule parks it because the blocker's `state_type` isn't "resolved". The drain then has nothing runnable and exits — even though the upstream *code exists on a branch* and could be built on.

*The change.* Widen the "resolved" definition used by the defer rule: an upstream issue whose **PR is open** (optionally: open *and* green CI) counts as resolved-enough to unblock downstream. Then point the downstream worktree's base at the upstream **branch** (`aba-A`) instead of `main`, so the agent builds on the unmerged work. Concretely: a defer-policy tweak in `linear.py` + a base-branch selection tweak in the orchestrator's Graphite handoff (it already chains `last_branch_per_repo` for in-run stacks; this extends that to branches whose issues are Done-with-open-PR but not yet merged).

*The cost it buys.* When A's PR changes in review, `aba-B`/`aba-C` were built on stale `aba-A` → they need `gt restack` and possibly re-execution. That rework risk is exactly the assumption to measure before committing (see below).

**Gear 2 — Trust-tiered auto-merge.** A Linear label (e.g. `auto-merge`) marks issues whose PRs may merge themselves *only after* CI + `/code-review-and-quality` pass clean. Default: gated. For trusted issues the gate self-clears via `gh pr merge --auto` (or Graphite merge queue), so downstream merges naturally.

**Gear 3 — Resident daemon (the destination).** drain-cycle becomes a long-running process that owns the full lifecycle without owner relaunch: it watches its own PRs, on merge it (a) `gt restack`s affected downstream and (b) picks up newly-runnable work; on review feedback it routes the PR back through a revise-and-resubmit loop. This is where the future PR-review lifecycle lives. Interim, before the resident process exists, Gears 1–2 can run under a cron/`/loop` relaunch of the existing transactional orchestrator — same observable behaviour for the merge-watch slice, far less to build — but that is scaffolding toward the daemon, not a substitute for it.

*The revise-and-resubmit loop, concretely.* Per watched PR: read review comments via the Graphite MCP → for each *new* comment, revise in the issue's existing worktree → re-submit to the stack → append `{comment_ids[], invoked_at, result}` to the run-log's `responder_runs[]`. The loop is **idempotent**: "new" means a comment ID absent from every prior `responder_runs[].comment_ids[]` for the issue, so the same comment is never addressed twice across passes, and a pass with no new comments exits cleanly without changes. This is a daemon behaviour, not a pack skill — only a resident process watches a PR after it is open (design decision §23, which supersedes the earlier `/shape:pr-respond` skill framing). The `responder_runs[]` field already exists in the run-log schema (shipped empty); Gear 3 is what first writes to it.

## Key Assumptions to Validate
- [ ] **Review rarely invalidates upstream.** Measure: over the last N drained stacks, how often did review force upstream changes that would have invalidated optimistically-stacked downstream? If high, Gear 1 amplifies wasted work — kill it.
- [ ] **Trusted classes are reliably classifiable.** Test with a single conservative label on a few low-risk issues; confirm gated-by-default holds and CI+review gate is enforced before any auto-merge.
- [ ] **Interim relaunch is good-enough scaffolding.** Confirm a cron/`/loop` relaunch keeps the cycle moving while the resident daemon is built — i.e. it's a fine bridge, not that it replaces the daemon.
- [ ] **Graphite restack is safe unattended.** Verify `gt restack` after an upstream merge resolves cleanly often enough to run without a human; define the halt-and-park fallback for conflicts.

## MVP Scope
Ship **Gear 2 on the interim relaunch host** first — removes the gate where it's safe with the least new logic, and de-risks the daemon before building its liveness:
- In: an `auto-merge` Linear label; after Done + green CI + clean review skill, the orchestrator enables `gh pr merge --auto`; existing drain relaunched via `/loop` or `CronCreate` so newly-unblocked work is picked up.
- Out (deferred to later gears): optimistic stacking on unmerged branches (Gear 1), automatic restack-on-review-change, and the resident daemon + PR-review lifecycle (Gear 3).
- Core assumption tested: does auto-merging trusted classes + relaunch clear the serialization barrier for *most* cycles? If yes, Gear 1's rebase risk may be unnecessary and the daemon can focus on the review lifecycle rather than unmerged-stacking.

## Not Doing (and Why)
- **Resident daemon *first*** — it's the destination, but building liveness/restart-state/stop-hook handling before the merge-and-trust logic is proven inverts the risk order. Use the relaunch host to validate Gears 1–2, then build the daemon to own them + the PR-review lifecycle.
- **Optimistic stacking, initially** — highest rework-amplification risk; gate it behind the measurement above rather than building it on faith.
- **Auto-merge by default / label-alone** — the expensive failure is a risky PR on `main`; trust must be opt-in *and* CI/review-gated.
- **Hand-rolled merge queue** — Graphite has one; prefer it over bespoke serialization if Gear 2 needs queuing.

## Open Questions
- Is "open PR" the right resolved-signal for Gear 1, or should it be "PR + green CI"?
- Trust as a Linear label, an issue-type, or a per-issue risk score from the review skill?
- For Gear 3, what's the merge-detection trigger — poll `gh`, a GitHub webhook, or piggyback on the existing cron pass?
- When a restack conflicts, halt-and-park the chain (current behaviour) or escalate differently?
- For the revise-and-resubmit loop: what's the Graphite MCP shape for reading review comments — comment-ID format, pagination, and the "unresolved" filter? (Flagged unverified by the cancelled ABA-331; verify against Graphite docs before building.)
- When the daemon's revision for a comment is itself wrong, what's the escalation path — halt-and-flag the PR for the operator, or retry with a stronger model tier? The operator must not be left silently looping on a bad fix.
