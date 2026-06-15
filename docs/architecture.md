# Architecture

Pre-read: [docs/vision.md](vision.md)

Shipping a piece of agentic work is never one action — it is a sequence: code it, review it against the right standards, open the PR, make the description read well. A capable agent handles any one of these steps, one at a time, and driving each by hand holds up fine while the work is small. As the work grows more complex, that hand-driving breaks down — and what breaks reveals that the operator was holding three fused jobs together: the **state** (where the work stands), the **steps** (what runs, in what order, to what standard), and the **supervision** (moving each piece from one step to the next). Holding all three by hand carries two costs: execution quality swings with the operator's discipline on the day, and the operator must drive every transition by hand instead of planning the next work.

So the question this architecture answers: how do you build the thing that holds the work together once — so the workflow runs the same way every time, without a person driving it — and how do the pieces of that system fit together?

The answer is two layers over one shared record. **Layer 1 — supervision** (`drain-cycle`) drives the work and grades each result. **Layer 2 — workflow** (the Shaper pack) is the procedure itself, captured as composable skills. They meet at an **artifact boundary**: Layer 2 writes signals, Layer 1 reads them, and neither reaches inside the other. Both work off one **state plane** — a single authoritative record of where the work stands. The move that makes this real is small: `prompt.py` stops inlining the workflow and becomes a thin pointer at the workflow's front door, `exec:pickup`. The pack then owns the procedure, so any vendor's worker follows the same prose. The payoff removes both costs above — the workflow is captured once and a content-blind supervisor runs it unattended, so execution is consistent on every run instead of varying by the day, and the operator is needed only to point the system at planned work and to step in when a run halts.

![Architecture overview: the operator points the control plane at planned work; a control plane (one per machine) spawns an execution-coordinator per unit; the coordinator drives a planned unit (project ⊃ milestone ⊃ task) whose task runs three Layer 2 phase spawns — exec:build, exec:review, exec:finish — while review rolls up the task, milestone, and project altitudes. Work fans down; verification rolls up.](images/architecture-overview.svg)

## 1. Split the work into two layers, one product

The work splits into two layers:

- **Layer 1 — supervision (`drain-cycle`).** Picks up a body of planned work and spawns a worker per phase per issue, holding the guardrails — halting, reverting, resuming, or recovering as the run demands. It then records and grades each outcome. It is vendor-agnostic: each worker is a `claude -p` (or any equivalent) subprocess.
- **Layer 2 — workflow (the Shaper pack).** The intra-issue procedure, captured as composable skills: how a piece of work goes from picked-up to merged PR. This is the knowledge that used to live in the operator's head, written down once.

The vision's three pieces map onto this split: "one place for state" is the state plane (§7), "the steps become skills" is Layer 2, and "the supervision becomes a supervisor" is Layer 1.

## 2. Layer 2 captures the workflow as composable skills

The pack carries two verb namespaces, one per half of the lifecycle:

- **Planning — `shape:*`.** Four front-door skills: `shape:idea`, `shape:project`, `shape:design`, `shape:delivery`. (The "Consolidate Shaper's Lifecycle into Four Phase Skills" project tracks this work.)

- **Execution — `exec:*`.** The intra-issue graph, named delegations only, no inlined procedure:
  
  ```
  exec:pickup → exec:breakdown → exec:build → (exec:debug | exec:simplify)
              → exec:review → exec:verify → exec:finish
  ```
  
  `exec:pickup` is the front door; `exec:review` fans out reviewer personas; `exec:finish` is Graphite-first with a plain-git fallback and produces the trail artefacts (What/Why/Focus PR body, review-summary comment, Linear status move). The execution namespace is pinned by ADR 0004 / the Shaper `execution-workflow` design doc; that doc is authoritative for the graph and the handoff contract.

Every step is also a standalone skill, so a human can run any one by hand. A **handoff envelope** carries the issue's acceptance criteria from pickup through to review, so the spec-compliance persona can grade *built-the-wrong-thing*, not just *built-it-badly*.

## 3. Layer 1 owns process, not procedure

`drain-cycle` owns process concerns only: spawn, guardrails, halt, revert, resume, recover, record, grade. It reads Linear state and the worker's artifacts to decide whether to advance to the next issue or stop. It does not contain workflow prose — the worker's prompt points at the entry skill and the procedure lives in Layer 2.

The supervisor mechanics are the bulk of `docs/design-decisions.md`: worktree-per-issue (§3), resource guardrails (§9), group-kill on breach (§8), resume-on-rerun (§14), blocks-aware ordering (§12), and the watch overlay (§15).

## 4. Spawn a worker per phase, not per issue

A worker does not run the whole `exec:*` chain. Each phase — code, review, finish — is its own spawn, with its own model tier, and the agent that produced an artifact never judges it. Two independent reasons force this:

- **Independent verification.** A coder reviewing its own work rationalises its own choices. Spawning review as a separate agent that did not write the code makes review adversarial by construction, not by prompt wording.
- **Per-phase model economics.** Review anchors to a stronger (more expensive) model regardless of what coded the change. That asymmetry is only expressible if each phase is its own spawn with its own `--model` pin — a cheap model can build while an expensive one reviews.

The three phase agents map onto the existing pack: code = `exec:build` (+`exec:debug`, `exec:simplify`), review = `exec:review`, finish = `exec:finish`. Each is a goal-shaped worker prompt, not a resident process — the goal ("produce sliced artifacts", "apply every quality lens", "land human-readable PRs") drives a sequence of skill delegations inside one spawn.

The cost accepted in return: every phase boundary pays a spawn plus artifact rehydration, so the handoff envelope (§5) must carry everything the next phase needs — a single worker kept that context in memory for free. This is the deliberate price of independence; see design decision §20.

## 5. Layer 2 writes signals; Layer 1 reads them

The two layers meet at an artifact, not at a function call. **Layer 2 writes signals; Layer 1 reads them.** The supervisor never reads the workflow's steps — only the artifacts the workflow leaves behind: Linear issue state, run-log fields, and `.drain-handoff.json` (e.g. `pr_urls`).

The placement test for any new behaviour: does `drain-cycle` need to know *what a role does* (Layer 2), or only *whether an artifact exists* (Layer 1)? Submitting a PR is "what a role does" — it belongs in a skill. Reading back the submitted PR URLs to decide whether to advance is "whether an artifact exists" — it belongs in the supervisor (see design decision §19).

Keeping the boundary at the artifact is what makes Layer 1 content-blind, and content-blindness is what lets the same Layer 2 run under any worker.

![The artifact boundary: Layer 2 (the workflow pack) writes signals down onto the boundary — Linear issue state, run-log fields, and .drain-handoff.json (pr_urls) — and Layer 1 (the supervisor) reads them to decide whether to advance or halt. Layer 1 never reads the workflow's steps, only the artifacts.](images/artifact-boundary.svg)

## 6. Dual-mode: the same skills by hand or unattended

Because the workflow is skills and the supervisor only reads artifacts, the same skills run two ways: the operator invokes them at the keyboard, or a spawned worker runs them unattended. There is one workflow, exercised two ways — not a manual path and a separate automated path that drift apart.

Design decision §10 (config symlinked into each worktree) is what makes the headless worker's environment match an interactive session, so a skill behaves the same in both modes.

## 7. All state lives in one record — the state plane

Everything works off one organized record of where the work stands. **Linear is authoritative** for issue status (design decision §1, §12). Beneath it sit the durable supervisor records: per-run logs (§4, §8), the atomic active-run marker (§11), and opt-in OpenTelemetry traces (§13).

These records are the **delayed feedback loop** that replaces live watching. Automating supervision deliberately gives up the operator's real-time view of each worker; observability is how that trade is paid back. Better observability widens the set of work that is safe to hand off (§9).

## 8. `prompt.py` becomes a pointer, not a script

The move that realizes the whole split: **`prompt.py` becomes a thin pointer at `exec:pickup`** instead of inlining the workflow. The supervisor stops carrying procedure; the pack owns it; any vendor's worker follows the same prose. This is tracked by the "drain-cycle supervises; the pack owns the workflow" project and is the run-first step — everything else in this doc is a facet of it. Until it lands, the supervisor's prompt still names a few skills directly (e.g. the current `/code-review-and-quality` and `/shape:pr-finishing`); those references swap to `exec:*` at the keystone cutover.

## 9. Extend the autonomy horizon as observability earns trust

How far the supervisor runs without the operator. Today it drains a cycle transactionally — one issue, one worker, one diff — and halts when it runs out of runnable work or hits a result it cannot get past. The horizon extends outward as observability earns more trust: building on unmerged work, auto-merging trusted classes, and eventually a resident process that watches its own PRs through the review-and-merge loop. That extension is shaped in [`ideas/drain-past-the-merge-gate.md`](ideas/drain-past-the-merge-gate.md).

The horizon is a deliberate trade, not a default: autonomy is applied selectively where the delayed feedback loop (§7) makes the loss of live intervention acceptable.

## 10. From a one-shot CLI to a resident daemon

The supervisor is moving from a one-shot CLI to a resident process — the autonomy horizon of §9 made concrete. Two scopes, both Layer 1:

- **Control plane (one per machine).** The long-lived daemon. Owns process lifecycle, the queue of planned units to execute, and an API the operator queries and steers: what is running, halt this issue, resume, and (the horizon behaviour) watch open PRs through the review-and-merge loop and respond to review comments.
- **Execution-coordinator (one per unit in flight).** Spawned by the control plane to drive a single cycle or project. It advances work by reading artifacts (§5) — never by reading inside a phase — and halts on a missing or failed artifact.

The control plane stays a **process, not a Claude skill** (design decision §22). A `/execute-cycle` skill would run inside a Claude session and collapse the artifact boundary that makes the worker vendor-agnostic; the one-command ergonomics come instead from a thin CLI front-door (`drain-cycle run <unit>`). "Respond to PR comments" is a control-plane behaviour, not a pack skill, because it requires watching a PR after it is open — which only a resident process does.

## 11. Execute a planned unit — a cycle or a project

The supervisor executes a *planned unit*. The atom is unchanged — one issue, with a worker per phase (§4) — and a cycle and a project differ only as containers with a hierarchy over them. "Drain a cycle" is one entry point, not the definition; project execution is out of scope today but is a later container, not a redesign (design decision §22). The tool keeps the name `drain-cycle`; the concept it serves is wider.

## 12. Roll review up the tree: multi-altitude review

`shape:delivery` decomposes committed work *downward* — project → milestones → nodes → tasks. Verification rolls *upward* along the same tree, with the review altitude matching the decomposition altitude:

| Altitude             | Fires when                                   | Lenses                                                                                                                             |
| -------------------- | -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| **Task** (per issue) | a task's diff is ready, before its PR merges | spec-compliance · security · reliability/resilience · code-quality · outcome — diff-bounded                                        |
| **Milestone**        | a milestone's last child task lands          | integration/coherence/acceptance (the task lens raised a level) · regression (did landing degrade anything outside this boundary?) |
| **Project**          | a project's last milestone lands             | architecture review · measurable stated goals (partial — some goals take time and are deferred)                                    |

Work fans down; verification rolls up. The trigger is structural — a parent's review fires exactly when its last child completes — and the hierarchy itself is authoritative in Linear (milestones, projects), so the state plane (§7) reads it rather than holding a second copy.

Three consequences distinguish higher-altitude review from task review:

1. **The execution-coordinator is a tree walker, not a queue drainer.** It models the hierarchy, detects "this milestone's last child landed", and fires the milestone review.
2. **Higher-altitude review produces new work, not reverts.** A task review can halt before a PR merges. A milestone or project review runs *after* its child PRs have merged — a merged slice cannot be cleanly reverted — so a failing review emits new remediation issues slotted back into the plan. This is a different halt semantic from task-level, and it widens the open seam below.
3. **Project review is partial and deferred.** Some goals cannot be measured at completion. Project review measures what it can now and *schedules* the rest, which is only possible with the resident control plane (§10).

Two unknowns here are not yet decided and are shaped as spikes, not committed: the **blast-radius definition** for regression review (it is not diff-bounded like task review), and **remediation routing** (what gets created on a failing altitude review, and whether the parent pauses or its siblings keep draining). See design decision §21.

## Decide the verdict-handoff schema before the correctness work

The one undecided boundary crossing: how Layer-2 verdicts (`outcome_verdict`, `prep_verdict`) travel from a skill into the run-log. Today `handoff.py` carries only `pr_urls`. The verdict-handoff schema is a one-way door for the correctness work (the "Multi-agent collaboration for correctness" Layer-1 project) and should be decided before that work starts.

The multi-altitude reviews (§12) widen this same seam: a milestone or project verdict has to cross the boundary too, and a failing one routes *remediation work* back into the plan rather than recording a pass/fail on a single diff. Whatever schema resolves the task-level verdict handoff should be designed to carry the higher-altitude verdicts as well.
