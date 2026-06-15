# Architecture

Pre-read:[`docs/vision.md`](vision.md)).

## 1. Two layers, one product

The work splits into two layers:

- **Layer 1 — supervision (`drain-cycle`).** Picks up a body of planned work - spawns a worker per phase per issue, holds the guardrails, halts/reverts/resumes/recovers, and records and grades the outcome. It is vendor-agnostic: each worker is a `claude -p` (or any equivalent) subprocess.
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

## 9. Phase separation: a worker per phase, not a worker per issue

A worker does not run the whole `exec:*` chain. Each phase — code, review, finish — is its own spawn, with its own model tier, and the agent that produced an artifact never judges it. Two independent reasons force this:

- **Independent verification.** A coder reviewing its own work rationalises its own choices. Spawning review as a separate agent that did not write the code makes review adversarial by construction, not by prompt wording.
- **Per-phase model economics.** Review anchors to a stronger (more expensive) model regardless of what coded the change. That asymmetry is only expressible if each phase is its own spawn with its own `--model` pin — a cheap model can build while an expensive one reviews.

The three phase agents map onto the existing pack: code = `exec:build` (+`exec:debug`, `exec:simplify`), review = `exec:review`, finish = `exec:finish`. Each is a goal-shaped worker prompt, not a resident process — the goal ("produce sliced artifacts", "apply every quality lens", "land human-readable PRs") drives a sequence of skill delegations inside one spawn.

The cost accepted in return: every phase boundary pays a spawn plus artifact rehydration, so the handoff envelope (§2) must carry everything the next phase needs — a single worker kept that context in memory for free. This is the deliberate price of independence; see design decision §20.

## 10. The resident control plane

The supervisor is moving from a one-shot CLI to a resident process — the autonomy horizon of §8 made concrete. Two scopes, both Layer 1:

- **Control plane (one per machine).** The long-lived daemon. Owns process lifecycle, the queue of planned units to execute, and an API the operator queries and steers: what is running, halt this issue, resume, and (the horizon behaviour) watch open PRs through the review-and-merge loop and respond to review comments.
- **Execution-coordinator (one per unit in flight).** Spawned by the control plane to drive a single cycle or project. It advances work by reading artifacts (§2) — never by reading inside a phase — and halts on a missing or failed artifact.

The control plane stays a **process, not a Claude skill** (design decision §22). A `/execute-cycle` skill would run inside a Claude session and collapse the artifact boundary that makes the worker vendor-agnostic; the one-command ergonomics come instead from a thin CLI front-door (`drain-cycle run <unit>`). "Respond to PR comments" is a control-plane behaviour, not a pack skill, because it requires watching a PR after it is open — which only a resident process does.

## 11. Multi-altitude review: the dual of the delivery hierarchy

`shape:delivery` decomposes committed work *downward* — project → milestones → nodes → tasks. Verification rolls *upward* along the same tree, with the review altitude matching the decomposition altitude:

| Altitude             | Fires when                                   | Lenses                                                                                                                             |
| -------------------- | -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| **Task** (per issue) | a task's diff is ready, before its PR merges | spec-compliance · security · reliability/resilience · code-quality · outcome — diff-bounded                                        |
| **Milestone**        | a milestone's last child task lands          | integration/coherence/acceptance (the task lens raised a level) · regression (did landing degrade anything outside this boundary?) |
| **Project**          | a project's last milestone lands             | architecture review · measurable stated goals (partial — some goals take time and are deferred)                                    |

Work fans down; verification rolls up. The trigger is structural — a parent's review fires exactly when its last child completes — and the hierarchy itself is authoritative in Linear (milestones, projects), so the state plane (§6) reads it rather than holding a second copy.

Three consequences distinguish higher-altitude review from task review:

1. **The execution-coordinator is a tree walker, not a queue drainer.** It models the hierarchy, detects "this milestone's last child landed", and fires the milestone review.
2. **Higher-altitude review produces new work, not reverts.** A task review can halt before a PR merges. A milestone or project review runs *after* its child PRs have merged — a merged slice cannot be cleanly reverted — so a failing review emits new remediation issues slotted back into the plan. This is a different halt semantic from task-level, and it widens the open seam below.
3. **Project review is partial and deferred.** Some goals cannot be measured at completion. Project review measures what it can now and *schedules* the rest, which is only possible with the resident control plane (§10).

Two unknowns here are not yet decided and are shaped as spikes, not committed: the **blast-radius definition** for regression review (it is not diff-bounded like task review), and **remediation routing** (what gets created on a failing altitude review, and whether the parent pauses or its siblings keep draining). See design decision §21.

## 12. The execution unit: a cycle or a project

The supervisor executes a *planned unit*. The atom is unchanged — one issue, with a worker per phase (§9) — and a cycle and a project differ only as containers with a hierarchy over them. "Drain a cycle" is one entry point, not the definition; project execution is out of scope today but is a later container, not a redesign (design decision §22). The tool keeps the name `drain-cycle`; the concept it serves is wider.

## Known open seam

The one undecided boundary crossing: how Layer-2 verdicts (`outcome_verdict`, `prep_verdict`) travel from a skill into the run-log. Today `handoff.py` carries only `pr_urls`. The verdict-handoff schema is a one-way door for the correctness work (the "Multi-agent collaboration for correctness" Layer-1 project) and should be decided before that work starts.

The multi-altitude reviews (§11) widen this same seam: a milestone or project verdict has to cross the boundary too, and a failing one routes *remediation work* back into the plan rather than recording a pass/fail on a single diff. Whatever schema resolves the task-level verdict handoff should be designed to carry the higher-altitude verdicts as well.
