# Plan review: thin-supervisor-contract (ADR 0002)

## Plan reference

`docs/adrs/0002-thin-supervisor-contract.md` (ABA-370 / C-D1-N01 — Rule-A1 design doc). Re-review after two corrections: (1) the owner closed the first PR because the `flow` field and verify-flow routing had leaked in despite a de-scope decision; (2) the doc had been filed in the wrong repo (agent-skills-shaper) and now lands in drain-cycle, as ADR 0002, since the contract governs `drain_cycle/`'s prompt template, `orchestrator.py`, `flow.py`, `runlog.py`, and `.drain-handoff.json` is drain-cycle's own exit record.

The doc is rewritten so verification is **universal** — `flow.py` deletes, the `verify` label stops being a routing input, the handoff carries no `flow` field, and the `Labels` line drops from the prompt template. Decides three things: the ≤15-line worker prompt template's segment allocation (now 12 lines), `.drain-handoff.json` schema v2 (verdict fields + halt-reason taxonomy, no `flow`), and the process/workflow boundary chart for every concern `drain_cycle/` carries today. Extends the pack's A/N04 inter-skill handoff contract with the supervisor-seam columns. Blocks N02, N03, N04.

## Inputs

- **Appetite**: design doc gating a 3-node deliverable (N02–N04, plus B/C work that references the boundary chart); decision-once, not a single slice
- **Cynefin domain**: Complicated — the supervisor source has been audited (A/N04's binding audit + this doc's segment table; `flow.py`, `prompt.py:is_verify_flow`, `orchestrator.py` stack resolution re-verified against the live source this session); the right answer is reachable by analysis from one inspectable codebase
- **Tier**: Full — selected because the plan contains a one-way-door decision (handoff schema bound by two repos and a future-vendor worker) AND gates ≥3 downstream nodes

## Trigger

Auto-fire #4 (one-way-door decision — `.drain-handoff.json` schema is bound by drain-cycle and the pack's `exec:*` skills across two repos; a field rename costs a coordinated edit). Trigger #2 also fires (gates multiple independently verifiable nodes). The prior review is superseded, not amended: the design changed materially (a whole decision dimension removed) and the doc moved repos, so this is a fresh run on new evidence.

## B1 — Problem framing

Opens problem-first: the prompt today inlines the workflow it expects the worker to follow, creating two sources of truth and a vendor-portability hole; the handoff carries grade-points but not inspection-points. Measurable desired state tied to KR3 (`wc -l` + grep) and the run-log (one parseable entry per exit). **OVERTURNED** (no defect). Falsifying condition: a Problem section that led with "we will collapse `prompt.py`" before naming the failure mode — it does not. The universal-verification reframe strengthens the framing: the doc no longer carries a per-ticket opt-in it has to justify.

## B2 — Scope clarity

| Item | Verdict | Falsifying condition |
|---|---|---|
| Dropping the `Labels` line from the prompt template (not named in the original "segment allocation" decision as a removal) | **PARTIAL** | OVERTURNED if no pack skill or `exec:pickup` reads a label at worker runtime. Verified this session: `flow.py` was the *only* runtime label consumer; `repo:`/`model:`/`review:` resolve pre-spawn (`linear.py`/`model.py`/`repos.py`). The drop is within the segment-allocation decision's remit. SUSTAINED only if N02's end-to-end drain shows a skill reaching for a label the prompt no longer carries |
| `flow.py` deletion named in the boundary chart but the supervisor-side rewrite cost not quantified | PARTIAL | OVERTURNED if N02's task breakdown lists the `flow.py` deletion (and `prompt.py:is_verify_flow` / `_shape_task_directive`, `runlog.py`'s `flow` field) as a named slice; the ADR enumerates the removal surface, so the breakdown has the list |
| The doc carries the `halt_reason` taxonomy as a *closed set* | **OVERTURNED** | A closed set with a documented one-line extension path is the right shape — open strings drift silently across two repos; the closed set surfaces drift at parse |
| The schema fitness function points at `docs/adrs/references/drain-handoff-schema-v2.md` (the artefact ships with the ADR) | **OVERTURNED → resolved in-session** | The contract artefact lands at the ADR surface, not as a build-node deliverable; N03 + N04 read it as a contract reference. Schema re-validated this session against the in-doc Done example + a halt fixture (jsonschema) |

No SUSTAINED scope drift. The two PARTIALs are governance-of-N02 points, each with a named close at N02.

## B3 — Assumptions + evidence quality

| Assumption | Confidence (Gilad) | 5-min test or owner | Verdict |
|---|---|---|---|
| Verification on **every** drain is the accepted contract (no `verify` label, no opt-out) | **8** (owner decision recorded on ABA-370, 2026-06-15; the de-scope that closed the first PR, not an inference) | Owner comment of record | OVERTURNED |
| No worker or pack skill consumes any label at runtime, so the `Labels` line is safe to drop | 4 (read the source: `flow.py` was the sole runtime label branch and it deletes; `repo:`/`model:`/`review:` resolve before spawn) | `git grep -niE "labels?\b"` over the pack skills for a runtime label read; N02's drain exercises a skill end-to-end | PARTIAL — test named, fixture not yet authored |
| A/N04 is accepted and stable enough that this doc can extend its handoff table without competing | **5** (verified — A/N04 carries `status: accepted` and a closed plan-review) | Read A/N04's status field | OVERTURNED |
| `drain_cycle/orchestrator.py` resolves `stack` per-run from `--no-stack` + `push_to_main_repos` | 5 (re-verified this session: `stack = target_repo.name not in repos.push_to_main_repos and not no_stack`) | Live source read | OVERTURNED |
| The 12-line template skeleton survives the next plausible process-fact addition (cycle id, sandbox id) | 2 (assertion — measured at 12 lines for the present fact set only; 3-line margin) | N02's first build slice attempts the template emit; if a fact must be added immediately, the brake re-negotiates | PARTIAL — governed |
| `prep_verdict.route == "human-review"` corresponds to a halt rather than a Done-equivalent state | 0.5 (open question Q1 — default reasoning, not measured against a live `shape:pr-prepare` run) | Open question Q1's resolution at N04 wiring | PARTIAL — flagged as Q1, owner named |

No assumption below Confidence 4 blocks APPROVE: the sub-4 ones (Q1 at 0.5, template margin at 2) are each flagged as a named open question or governed at a downstream node. The load-bearing new assumption — no runtime label consumer — sits at 4 with a named test that runs before any code lands.

## B4 — Dependencies

| Dependency | Owner confirmed? | Capacity confirmed? | Verdict |
|---|---|---|---|
| The pack's `pickup-envelope.json` shape (the in-flight carrier this contract sits beside) | Same owner; A/N04 accepted; envelope columns owned in A/N04 | n/a | OVERTURNED |
| `drain-cycle` repo (this repo — consumes template + schema v2; emits per-run handoffs; deletes `flow.py`) | Same owner; landing path is N02 + N04, both drain-cycle | n/a | OVERTURNED |
| `agent-skills-shaper` (the pack — `exec:*` skills write the verdict fields, read the schema reference cross-repo) | Same owner; N03 lands there | n/a | OVERTURNED |
| Linear MCP + workflow governance | Installed | n/a | OVERTURNED |

No cross-team dependencies; single-owner workspace.

## B5 — Reversibility + ADR pairing

| One-way door | Alternatives in plan? | ADR exists / committed? | Verdict |
|---|---|---|---|
| `.drain-handoff.json` schema v2 (bound by two repos and a future vendor worker) | **Yes** — 3 alternatives (flat extension, versioned envelope, per-skill handoff files), each with blast radius + reversal cost; per-skill files rejected because they break the single grade-point the run-log commits to | This *is* ADR 0002 — the decision is the record | OVERTURNED |
| Universal verification (no per-ticket opt-out) | **Partial** — the doc names the cost (every drain pays the verify pipeline) and the reversal path (re-adding a lighter path is a *new contract decision, not a label toggle*). It does not enumerate a rejected "keep the label gate" alternative, because that alternative is the de-scoped prior design the owner already overturned | Owner decision recorded on ABA-370; the ADR cites it as the contract | OVERTURNED — decided one level up, not re-litigated here |
| Prompt template segment allocation (incl. dropping `Labels`) | **Yes** — 3 alternatives (process + minimal + pointer; inlined resume prose; pointer-only via env vars); pointer-only rejected on portability grounds | Two-way (one-file revert restores prior behaviour); kill condition encoded ("3 consecutive halts → restore inlined tail") | OVERTURNED |

The doc lands its one-way-door decisions with alternatives, named blast radius, and named reversal costs. Universal verification is correctly treated as a decision inherited from the owner, not re-opened.

## B6 — Operability + success metrics

- Metrics: **named** — `outcome_verdict.result` rate (now across every drain), `prep_verdict.route` split, `halt_reason` histogram, wall-clock per run
- Alerts: **named as deliberately none** — single-user CLI; halt surface is stderr + run-log (justified)
- Rollback path: **named** — 3 ordered steps (template revert, parser revert, taxonomy extension), each with a verification gate
- Runbook: n/a — single-user CLI; the run-log is the inspection surface
- Capacity headroom: **named** — handoff ≤4 KB/run; envelope ≤2 KB/drain (cited from A/N04); run-log trim cadence stated
- User-visible outcome metric: KR3 brake (mechanical, two greps — `wc -l` ≤15 and the pinned procedure-verb grep) + run-log schema-check one-liner — both outcomes, not delivery metrics

**OVERTURNED.** Operability survives the `flow` removal cleanly: the metrics that referenced `flow` distribution are replaced with per-run equivalents; every failure mode in the table still carries a fitness function.

## B7 — Sequencing + capacity

Critical path: this ADR → N02 (drain-cycle template + `flow.py` deletion) ‖ N03 (pack skills write verdicts) → N04 (supervisor records verdicts) → N05 (validation cycles). The blockedBy edges are bound as Linear edges at issue creation. Two open questions (Q1, Q2) defer cleanly to specific later nodes; the two `flow`-dependent questions (verify-only entry point; `flow` enum survival) are **removed** — they are moot under universal verification. Appetite-against-emit: this doc is the design surface for 5 nodes, under the initiative's ~7-issue cap.

No SUSTAINED.

## B8 — Pre-mortem

Assume the doc shipped and the initiative failed within its appetite. Top 3, ranked:

1. **(most likely)** A pack skill or `exec:pickup` turns out to need a label at worker runtime (e.g., review intensity), but the `Labels` line was dropped from the prompt and the label was resolved-and-discarded pre-spawn. The worker can no longer see it. *Kill-switch:* N02's first slice drains one issue end-to-end *before* the `Labels` line is removed from the live template; a skill reaching for a missing label surfaces the gap. Cheap recovery — re-adding one line stays under the 15-line cap (3-line margin).
2. Universal verification proves too heavy on trivial (doc-only) issues; throughput drops at N05 and the pressure is to re-introduce a `verify`-style opt-out label ad hoc — recreating the exact two-sources-of-truth failure this contract exists to end. *Kill-switch:* the doc states an opt-out is a *new contract decision, not a label toggle*; N05's wall-clock-per-run metric surfaces the cost, and any opt-out must come back through plan-review.
3. Schema v2 fields are written by `exec:verify` / `shape:pr-prepare` with the wrong shape (e.g., `failed_ac` becomes `{ac_id: reason}` in one writer, `string[]` in another). *Kill-switch:* the NFR table commits a schema-fitness check on every run; drift fails parse at the writer, landing at N03 schema-test authoring before N04 wires the supervisor read.

Each pre-mortem names a specific mode with an early-catch condition; none is generic. Risk #1 replaces the prior review's "`flow.py` collapse reappears in `exec:pickup`" — that mode is gone because `flow.py` is *deleted*, not relocated.

## Recommendation

**APPROVE** — the doc opens problem-first, names its decisions each with a clean alternatives table, pins KR3's grep pattern explicitly, and extends A/N04's handoff table in place (one source of truth across two repos). The `flow` field and verify-flow routing are fully removed: no `flow` in the schema (`required` and `properties` both), the boundary chart records `flow.py` as *deleted* (not relocated), and the two `flow`-dependent open questions are struck. The doc is filed in the correct repo (drain-cycle, ADR 0002). Schema re-validated this session against the in-doc Done example and a halt fixture. Every NFR carries a fitness function; the remaining open questions have named owners and resolution gates.

### Conditions

1. **(non-blocking, B2 + B8 #1)** N02's **first** slice drains one issue end-to-end *before* the `Labels` line is removed from the live template and *before* `flow.py` deletes — confirming no skill reads a label at worker runtime.
2. **(non-blocking, B2)** When N02's tasks are broken out, list the `flow.py` deletion (with `prompt.py:is_verify_flow`/`_shape_task_directive` and `runlog.py`'s `flow` field) as a named, single-revertable slice rather than folding it silently into the template edit.
3. **(resolved in-session, B6/B8 #3)** N03's pack-skill schema test reads the JSON Schema fenced inside `docs/adrs/references/drain-handoff-schema-v2.md` (landed with this ADR), not a future build-node artefact.

All three conditions are non-blocking. The doc is accepted; N02–N04 can break tasks against it.
