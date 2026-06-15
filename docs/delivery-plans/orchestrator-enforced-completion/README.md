# Delivery plan — Orchestrator-enforced completion

The delivery hierarchy for drain-cycle's completion-recovery work, emitted by `delivery-shape` per
the plan-artefact contract in the Shaper repo
(`~/src/agent-skills-shaper/docs/delivery-shape-contract.md`; gates: `bin/walk-delivery-plan`,
`bin/check-plan-framing`).

**Source:** drain-cycle (`~/src/drain-cycle`), single-task source — the stated outcome below is the
traceability spine. Project [Autonomous cycle
drain](https://linear.app/ababushkin/project/autonomous-cycle-drain-eliminate-manual-shepherding-75daa8863063)
· Linear `ABA`. Incident context: in cycle `793fc9ea`, issue ABA-383 ran on a `model:haiku` worker
that committed 4 reviewable slices but exited without running the drain-mode finishing protocol
(review → `/shape:pr-finishing` → `.drain-handoff.json` with `pr_urls` → Linear Done). The
orchestrator's not-Done halt (`orchestrator.py:781`) reverted the issue to Todo and halted the run,
stranding committed work behind a coarse "re-run the whole issue" recovery.

---

## The bet (read this first — top-down starts at the outcome)

**Goal:** A drain run never strands committed-but-unfinished work. When a worker exits leaving
reviewable commits on the issue branch but the issue is not properly closed, the orchestrator
itself drives completion via a fresh finishing sub-agent before it halts — and never trusts work it
cannot judge.

| KR | Claim | Role | Served by |
|----|-------|------|-----------|
| **KR1** *(commit)* | When a worker exits with committed slices beyond base but the issue is not properly closed (not Done, **or** Done-without-`pr_urls`), the orchestrator spawns a sonnet finishing sub-agent that runs review → `/shape:pr-finishing` → Linear Done (delegating any Critical/Required fix to opus), re-checks the contract, and only halts if completion still fails. A worker that left **no** committed slices — empty branch or uncommitted-only working tree — halts as today, untrusted. | bet | **D1** |

**Appetite:** one small Graphite stack of up to 3 PRs — one per task slice under N01, stacked so
each lands as an independently green, human-reviewable diff against `main`.

**Kill condition:** if a finishing sub-agent cannot be spawned and re-checked from inside
`_drain_one_issue` without the orchestrator itself running `gh`/`gt` (the AGENTS.md boundary the
worker owns), stop — completion recovery belongs in the worker contract, not the orchestrator, and
the halt-then-manual-finish path stands.

**Rule A1 check:** the single deliverable does not trip a trigger — it is one node, edits
drain-cycle-internal control flow only, touches no shared infrastructure, ships behind the existing
unattended-run path, and carries no user/cost/compliance impact. So it carries no `design-doc`
node; the binding design constraints ride in N01's body.

---

## How to read this plan top-down

```
orchestrator-enforced-completion (this README)   ← goal + outcome (KR1)
└── deliverable  D1/_deliverable.md               → milestone   (serves KR1)
    └── node  N01-recover-stranded-committed-work.md → issue     (story)
        └── task  - [ ] in node                       → sub-issue (`skeleton` opens the seam)
```

Directory nesting **is** the hierarchy; numeric `D*/N*` prefixes give deterministic ordering.

## Tree

```
orchestrator-enforced-completion/
├── README.md                                          ← you are here (plan root)
└── D1-recover-stranded-committed-work/                → milestone · serves KR1
    ├── _deliverable.md
    └── N01 … finishing sub-agent recovers committed work  · story · skeleton
```

## Hand-count manifest

The derived manifest from `bin/walk-delivery-plan` must reproduce these counts:

| Tracker artefact | Source layer | Count |
|------------------|--------------|------:|
| Milestones | deliverables (`D*`) | **1** |
| Issues | nodes (`N*`) | **1** |
| Sub-issues | tasks (`- [ ]` lines) | **3** |
