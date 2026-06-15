# Delivery plan — Watch-pane rich rendering

The delivery hierarchy for the drain-cycle watch-pane rendering work, emitted by
`delivery-shape` per the plan-artefact contract in the Shaper repo
(`~/src/agent-skills-shaper/docs/delivery-shape-contract.md`; gates: `bin/walk-delivery-plan`,
`bin/check-plan-framing`).

**Source:** drain-cycle (`~/src/drain-cycle`), project [Watch pane: Claude-Code-style
rendering](https://linear.app/ababushkin/project/watch-pane-claude-code-style-rendering-80c86f79c7ee)
· Linear `ABA`. Investigation context: the `-w` pane pipeline is
`claude … --output-format stream-json | tee <fifo> | { <venv-py> -u -m drain_cycle.watch_format || cat; }` —
the `tee` FIFO branch feeds drain-cycle's parser byte-for-byte, so the pane copy is pure display
and nothing rendered there can affect logging, usage accounting, run logs, or telemetry. The
existing formatter (`drain_cycle/watch_format.py`) works but is terse (`=== Turn N ===`,
`→ Tool(kwargs…)`, `← N chars`).

---

## The bet (read this first — top-down starts at the outcome)

**Goal:** An operator watching `drain-cycle -w` reads each worker session as a Claude-Code-style
transcript instead of raw stream-json or terse `===` headers — with zero impact on parse
fidelity, usage accounting, or run logs.

| KR | Claim | Role | Served by |
|----|-------|------|-----------|
| **KR1** *(commit)* | The pane renders assistant prose and tool calls as ⏺ bullets with per-tool meaningful arguments, ⎿ result previews, and ✻ thinking markers — verified by unit tests plus a tmux `capture-pane` replay of a captured real session. | bet | **D2** |
| **KR2** *(commit)* | Zero observability regression: the filter exits 0 and degrades to raw-line echo on any malformed or pathological event (crash-resilience tests), the FIFO branch and pipeline string are untouched, and `tests/test_orchestrator_watch.py` passes unchanged. | brake | **D1** |
| **KR3** *(commit)* | The pane shows live per-turn token/context status and an end-of-run cost footer whose numbers match `worker._UsageAccumulator` on the same fixture stream. | foundation | **D3** |

**Appetite:** one small Graphite stack of 5 PRs — one per node, stacked
N01 → N02 → N03 → N04 → N05 so each lands as an independently green, human-reviewable diff.

**Kill condition:** if rendering fidelity demands stateful terminal control (cursor movement,
redraw, live-updating regions), stop — that is a TUI, not a pipe filter; keep the terse
formatter and close the remaining nodes unstarted.

**Rule A1 check:** no deliverable trips a trigger — each is ≤3 nodes, display-only, fully
reversible, no shared infrastructure, no user/cost/compliance impact — so no deliverable carries
a `design-doc` node. The binding design constraints ride in each node's body instead.

## How to read this plan

Top-down: this README (outcome), then each `D*/_deliverable.md` (which KR it serves and why),
then the `N*.md` nodes (issue-class work units, each with What / Why / Completion / Assumptions /
Key Risks and a coarse task list). Fine-grained build tasks are deferred to pickup. Graphite
stack order across deliverables is carried by `> **Blocked by:**` callouts in each node.

## Tree

```
watch-pane-rich-rendering/
├── README.md
├── D1-filter-never-breaks-the-pane/
│   ├── _deliverable.md
│   └── N01-crash-proof-filter.md
├── D2-claude-code-style-transcript/
│   ├── _deliverable.md
│   ├── N02-transcript-rendering-core.md
│   ├── N03-ansi-color-layer.md
│   └── N05-docs-pane-rendering.md
└── D3-live-usage-status/
    ├── _deliverable.md
    └── N04-usage-status-and-footer.md
```

## Linear issues

Created from the nodes (filled in after creation):

| Node | Linear issue |
|------|--------------|
| N01 | [ABA-386 — Watch pane: crash-proof formatter filter](https://linear.app/ababushkin/issue/ABA-386/watch-pane-crash-proof-formatter-filter) |
| N02 | [ABA-387 — Watch pane: Claude-Code-style transcript rendering core](https://linear.app/ababushkin/issue/ABA-387/watch-pane-claude-code-style-transcript-rendering-core) |
| N03 | [ABA-388 — Watch pane: ANSI colour layer](https://linear.app/ababushkin/issue/ABA-388/watch-pane-ansi-colour-layer) |
| N04 | [ABA-389 — Watch pane: live usage status line + cost footer](https://linear.app/ababushkin/issue/ABA-389/watch-pane-live-usage-status-line-cost-footer) |
| N05 | [ABA-390 — Watch pane: docs + end-to-end pane replay](https://linear.app/ababushkin/issue/ABA-390/watch-pane-docs-end-to-end-pane-replay) |

Each issue is blocked by its predecessor (ABA-386 → 387 → 388 → 389 → 390), matching the
Graphite stack order. All created in Todo, deliberately **not** assigned to a cycle — pull them
in when ready to drain.

## Manifest (hand count — the walk-script's oracle)

| Tracker artefact | Source layer | Count |
|------------------|--------------|------:|
| Milestones | deliverables (`D*`) | **3** |
| Issues | nodes (`N*`) | **5** |
| Sub-issues | tasks (`- [ ]` lines) | **12** |
