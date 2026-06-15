# Architecture

How `drain-cycle` is built to serve the vision. The vision ([`docs/vision.md`](vision.md)) is the stable north-star — the *why*; this doc is the *how*, and it changes as the system does. The running decision log beneath it, with rationale and alternatives per change, is [`docs/design-decisions.md`](design-decisions.md).

These section numbers are stable anchors — other docs cite them (e.g. `§8` for the autonomy horizon).

## 1. Two layers, one product

The work splits into two layers, and `drain-cycle` is only the top one.

- **Layer 1 — supervision (`drain-cycle`).** Picks up a Linear cycle, spawns a worker per issue, holds the guardrails, halts/reverts/resumes/recovers, and records and grades the outcome. It is vendor-agnostic: the worker is a `claude -p` (or any equivalent) subprocess.
- **Layer 2 — workflow (the Shaper pack).** The intra-issue procedure, captured as composable skills: how a piece of work goes from picked-up to merged PR. This is the knowledge that used to live in the operator's head, written down once.

The vision's three pieces map onto this split: "one place for state" is the state plane (§6), "the steps become skills" is Layer 2, and "the supervision becomes a supervisor" is Layer 1.

## 2. The artifact boundary

The two layers meet at an artifact, not at a function call. **Layer 2 writes signals; Layer 1 reads them.** The supervisor never reads the workflow's steps — only the artifacts the workflow leaves behind: Linear issue state, run-log fields, and `.drain-handoff.json` (e.g. `pr_urls`).

The placement test for any new behaviour: does `drain-cycle` need to know *what a role does* (Layer 2), or only *whether an artifact exists* (Layer 1)? Submitting a PR is "what a role does" — it belongs in a skill. Reading back the submitted PR URLs to decide whether to advance is "whether an artifact exists" — it belongs in the supervisor (see design decision §19).

Keeping the boundary at the artifact is what makes Layer 1 content-blind, and content-blindness is what lets the same Layer 2 run under any worker.

## 3. Dual-mode: the same skills by hand or unattended

Because the workflow is skills and the supervisor only reads artifacts, the same skills run two ways: the operator invokes them at the keyboard, or a spawned worker runs them unattended. There is one workflow, exercised two ways — not a manual path and a separate automated path that drift apart.

Design decision §10 (config symlinked into each worktree) is what makes the headless worker's environment match an interactive session, so a skill behaves the same in both modes.

## 4. Layer 2 — the workflow pack

The pack carries two verb namespaces, one per half of the lifecycle:

- **Planning — `shape:*`.** Four front-door skills: `shape:idea`, `shape:project`, `shape:design`, `shape:delivery`. (Consolidation tracked by the "Consolidate Shaper's Lifecycle into Four Phase Skills" project.)
- **Execution — `exec:*`.** The intra-issue graph, named delegations only, no inlined procedure:

  ```
  exec:pickup → exec:breakdown → exec:build → (exec:debug | exec:simplify)
              → exec:review → exec:verify → exec:finish
  ```

  `exec:pickup` is the front door; `exec:review` fans out reviewer personas; `exec:finish` is Graphite-first with a plain-git fallback and produces the trail artefacts (What/Why/Focus PR body, review-summary comment, Linear status move). The execution namespace is pinned by ADR 0004 / the Shaper `execution-workflow` design doc; that doc is authoritative for the graph and the handoff contract.

Every step is also a standalone skill, so a human can run any one by hand. A **handoff envelope** carries the issue's acceptance criteria from pickup through to review, so the spec-compliance persona can grade *built-the-wrong-thing*, not just *built-it-badly*.

## 5. Layer 1 — the supervisor

`drain-cycle` owns process concerns only: spawn, guardrails, halt, revert, resume, recover, record, grade. It reads Linear state and the worker's artifacts to decide whether to advance to the next issue or stop. It does not contain workflow prose — the worker's prompt points at the entry skill and the procedure lives in Layer 2.

The supervisor mechanics are the bulk of `docs/design-decisions.md`: worktree-per-issue (§3), resource guardrails (§9), group-kill on breach (§8), resume-on-rerun (§14), blocks-aware ordering (§12), and the watch overlay (§15).

## 6. The state plane

Everything works off one organized record of where the work stands. **Linear is authoritative** for issue status (design decision §1, §12). Beneath it sit the durable supervisor records: per-run logs (§4, §8), the atomic active-run marker (§11), and opt-in OpenTelemetry traces (§13).

These records are the **delayed feedback loop** that replaces live watching. Automating supervision deliberately gives up the operator's real-time view of each worker; observability is how that trade is paid back. Better observability widens the set of work that is safe to hand off (§8).

## 7. The keystone

The move that realizes the whole split: **`prompt.py` becomes a thin pointer at `exec:pickup`** instead of inlining the workflow. The supervisor stops carrying procedure; the pack owns it; any vendor's worker follows the same prose. This is tracked by the "drain-cycle supervises; the pack owns the workflow" project and is the run-first step — everything else in this doc is a facet of it. Until it lands, the supervisor's prompt still names a few skills directly (e.g. the current `/code-review-and-quality` and `/shape:pr-finishing`); those references swap to `exec:*` at the keystone cutover.

## 8. The supervisor's autonomy horizon

How far the supervisor runs without the operator. Today it drains a cycle transactionally — one issue, one worker, one diff — and halts when it runs out of runnable work or hits a result it cannot get past. The horizon extends outward as observability earns more trust: building on unmerged work, auto-merging trusted classes, and eventually a resident process that watches its own PRs through the review-and-merge loop. That extension is shaped in [`ideas/drain-past-the-merge-gate.md`](ideas/drain-past-the-merge-gate.md).

The horizon is a deliberate trade, not a default: autonomy is applied selectively where the delayed feedback loop (§6) makes the loss of live intervention acceptable.

## Known open seam

The one undecided boundary crossing: how Layer-2 verdicts (`outcome_verdict`, `prep_verdict`) travel from a skill into the run-log. Today `handoff.py` carries only `pr_urls`. The verdict-handoff schema is a one-way door for the correctness work (the "Multi-agent collaboration for correctness" Layer-1 project) and should be decided before that work starts.
