# ADR 0023: The worker owns PR submission via the finishing skill; the orchestrator reads `pr_urls` back

**Date:** 2026-06-15
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §19

This reverses the actor in ADR 0020 and ADR 0022. There, the orchestrator assembled and submitted the per-repo Graphite stack and posted the Linear comment. Now the **worker** does it: in drain mode the worker commits reviewable slices, then runs the finishing skill (`/shape:pr-finishing` today; `exec:finish` after the keystone cutover, ADR 0028), which owns submission — it drives `gt`/`gh`, writes the submitted PR URLs into `.drain-handoff.json` (`pr_urls`), and posts the review-summary comment on the Linear issue. The orchestrator no longer assembles or submits anything; it **reads `pr_urls` back** as confirmation that submission succeeded, and a Done stack-mode issue with no `pr_urls` halts the run rather than letting the next issue stack on an unpushed branch.

**Why the reversal.** It is the artifact boundary applied ([`architecture.html`](../architecture.html) §5). Submitting a stack is "what a role does" — Layer 2 — so it belongs in a skill that runs identically by hand or unattended. Reading back whether the PRs exist is "whether an artifact exists" — Layer 1 — so it stays in the supervisor. The ADR 0020 / ADR 0022 design put a Layer-2 action inside Layer 1, which is exactly the coupling the two-layer split removes: an orchestrator that knows the `gt`/`gh` sequence cannot be the thin, vendor-agnostic supervisor the keystone (ADR 0028) requires.

**The verified `gt`/`gh` sequence in ADR 0020 is still correct** — it is just run by the finishing skill, not the orchestrator. ADR 0020's per-repo preconditions (`gt auth`, `gt init --trunk main`) and its stop-the-line restack policy carry over unchanged.

**Completion recovery preserves the boundary.** When a worker exits leaving committed slices but the issue is not properly closed (not Done, or Done-without-`pr_urls`), the orchestrator does not run `gt`/`gh` itself — it spawns a fresh finishing sub-agent that runs the skill, then re-checks the contract, and only halts if completion still fails. A worker that left no committed slices halts as untrusted. (Tracked by the "Orchestrator-enforced completion" delivery plan.)

**Alternatives considered.**

- *Keep the orchestrator assembling the stack (ADR 0020 / ADR 0022 as written).* Rejected: it hard-codes the PR-tooling sequence into Layer 1, blocking the keystone and the vendor-agnostic worker.
- *Worker pushes by hand instead of via the skill.* Rejected: the skill is the single place the submission procedure lives, so it stays identical in interactive and drain modes; a hand-rolled push in the worker prompt would be a second, drifting copy.
