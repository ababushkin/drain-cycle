# Architecture diagrams

These diagrams illustrate [`../architecture.html`](../architecture.html). The committed `.svg` files are clean, first-pass renders that display on GitHub today. They are meant to be **redrawn in the hand-drawn Excalidraw aesthetic** later; this file is the label/layout spec to redraw from, so the wording stays 1:1 with the doc.

**Rule that keeps the visuals honest:** the doc is authoritative. Every label here appears verbatim in `architecture.html`. Do not introduce friendlier or invented names when redrawing — if a term needs to change, change the doc first, then the diagram.

## Diagram 1 — `architecture-overview.svg` (apex visual, after the lead)

A top-to-bottom flow from the operator into the supervision layer, then into one planned unit, with review rolling back up.

- **operator** → **Control plane (one per machine, resident daemon).** Layer 1. Owns process lifecycle and the queue of planned units; exposes a steer API (what's running · halt · resume); watches open PRs through the review-and-merge loop and responds to PR comments (horizon behaviour — only a resident process does this).
- Control plane **spawns one Execution-coordinator per unit in flight.** Layer 1, a tree walker. Advances work by reading artifacts (§5), never by reading inside a phase; halts on a missing or failed artifact.
- The coordinator drives one **planned unit**, drawn as nested containers: **PROJECT ⊃ MILESTONE ⊃ TASK (one issue).**
- Inside the task, three **phase spawns** run in sequence, each a Layer 2 skill:
  - `exec:build` — goal: produce sliced artifacts (+ `exec:debug`, `exec:simplify`)
  - `exec:review` — goal: apply every quality lens (fans out reviewer personas)
  - `exec:finish` — goal: land human-readable PRs (What/Why/Focus PR · summary comment · status move)
  - Annotate the build→review boundary: **"the agent that produced an artifact never judges it."**
- **Review altitudes** sit to the right, each aligned to its container:
  - **Task review** — diff-bounded, before the PR merges: spec-compliance · security · reliability/resilience · code-quality · outcome.
  - **Milestone review** — integration/coherence/acceptance · regression (outside this boundary); after child PRs merge → new work, not reverts.
  - **Project review** — architecture review · measurable stated goals (partial — some goals deferred).
- Edge arrows: **work fans down (decomposition)** on the left; **verification rolls up** on the right.
- Colour key: Layer 1 = blue, Layer 2 skills = green, review = amber.

## Diagram 2 — `artifact-boundary.svg` (supports §5)

Two stacked boxes with the boundary between them.

- **Layer 2 — the workflow pack (skills)** on top: does the work, then leaves signals behind. **WRITES ↓.**
- **The artifact boundary** is a dashed line carrying three artifacts: **Linear issue state · run-log fields · `.drain-handoff.json` (`pr_urls`).**
- **Layer 1 — the supervisor** below: advances to the next issue, or halts, on whether an artifact exists. **READS ↓.**
- Caption: Layer 1 never reads the workflow's steps — only the artifacts it leaves behind; that content-blindness lets the same Layer 2 run under any worker.

## Diagram 3 — multi-altitude review (deferred)

Not drawn. The overview (Diagram 1) already shows the fan-down/roll-up duality and all three review altitudes, and `architecture.html` §12 carries the per-altitude lens table. Add a dedicated diagram only if a future reader finds the roll-up idea underserved by those two.
