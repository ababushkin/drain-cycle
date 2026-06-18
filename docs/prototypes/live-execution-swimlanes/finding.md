# Prototype finding: live-execution-swimlanes

**Track:** C — product spike (narrative mode)
**Upstream:** `docs/ideas/live-execution-swimlanes.md`
**Recommendation:** **Proceed** → `shape:design` Track A (design document)

## Question
Glancing at a vertical nested tree of the live run, can the operator instantly tell which `exec:*` step and persona is active — and what's done vs upcoming — well enough to trust the run without reading the scrolling log?

## Approach
Narrative mode. No code (a coded prototype would depend on the observability plumbing the upstream record flagged as an unproven assumption). Built three annotated ASCII mockups of the rendered terminal output, all showing the **same live moment** — ABA-313 mid-`review`, `security-auditor` running — varying only on the axis that drives trust: how much of the chain is visible and how the active node is emphasized. The operator (the user) was the observer; their reaction across two rounds is the observation.

- **Round 1** — three layouts: (A) full vertical status tree, whole `exec:*` chain always drawn with per-node state marks; (B) progressive append-only log-tree, closest to the operator's original sketch, upcoming steps not shown; (C) compact horizontal stepper spine + vertical drill-down of the active step's personas.
- **Round 2** — refined the round-1 winner on the remaining fork (coexistence with the live worker output): (C-pinned) tree pinned above a scrolling raw log vs (C-status-only) tree only, redraws in place, active node carries a one-line sub-status for proof-of-life.

Time-box: 1–2 build-observe iterations. Kill condition: no layout reads cleanly at a glance, or a flat-log improvement would do the job instead. Set before building; not extended.

**Follow-up iteration (operator-requested, throwaway-code mode).** After the narrative spike resolved the single-issue layout, the operator asked to see the layout *in motion* and then to extend it with a cycle-level queue: "see all the tasks in the queue and toggle between them for viewing… in execution order, which takes into account dependencies." Built `demo.py` — a non-production Python/`rich` animation that drains a four-issue cycle, reading the real `agent-skills-shaper` pack for step captions and review personas. It adds a **queue pane** (issues in dependency-resolved execution order, lane state per issue) above the focused issue's swimlane, with **keyboard toggle** (`↑/↓`/`1-N` to select, `f` to re-follow the running issue, `space` pause). This was a build-observe slice on the queue/toggle question, not a re-opening of the layout question.

## Raw observations
<!-- What was seen, not what it means. -->
- Round 1: operator chose **C** (stepper + active drill-down) over A and B.
- Round 1: the choice **contradicts a constraint the operator stated during intake** — "vertical UI rather than horizontal Kanban." C's chain spine is horizontal. Shown concrete options, the operator picked the horizontal spine anyway.
- Round 1: B was the layout that literally matched the operator's own ASCII sketch from intake; it was not chosen.
- Round 2: operator chose **C-status-only** (tree only, redraws in place, no scrolling log in default mode) over C-pinned (tree + scrolling raw log).
- Round 2: the horizontal spine was re-confirmed as acceptable (chosen a second time) when the contradiction was named explicitly.
- Follow-up: after seeing the single-issue layout animate, the operator asked — unprompted — to add a **cycle queue** above the swimlane and to **toggle** which issue is viewed; and that the queue be ordered by **dependency-aware execution order**. The single-issue swimlane was not enough on its own: the operator wanted run-wide context (what's done, running, waiting) alongside the active issue's detail.
- Follow-up: the requested toggle is *viewing*, not *steering* — switching which issue's detail is shown, while execution stays sequential (one issue runs at a time). The operator did not ask to run issues in parallel or reorder them.

## Interpretation
The winning design is **a compact horizontal stepper for the linear `exec:*` chain + a vertical drill-down of the active step's persona fan-out, rendered status-only and redrawn in place** — no scrolling raw log in the default (non-`-w`) output; the active node carries a one-line sub-status so the run still shows proof-of-life. Raw streaming stays the job of `-w`.

The stated constraint ("vertical, not horizontal kanban") was a **want-vs-said miss**. The real need is *compact, glanceable run state*, not verticality for its own sake. A horizontal spine fits the chain's linear shape; verticality earns its place only for the fan-out (personas under a step), where depth is real. The literal sketch (B, fully vertical, append-only) was rejected because append-only hides "what's upcoming" — the exact signal the trust outcome needs. So the operator's own first-draft solution did not survive contact with alternatives, which is the point of running the spike before building.

The queue/toggle follow-up extends — not contradicts — that finding. The swimlane answers "where is *this* issue," but a drain runs a whole cycle; the operator also needs "where is the *run*." So the surface is two-level: a **cycle queue** (every issue, in dependency-resolved execution order, with lane state `done ●` / `running ◉` / `queued ○` and a dependency note) sitting above the **focused issue's swimlane**. The toggle lets the operator inspect any issue's lane without losing the run's shape, and auto-follow keeps the running issue in view by default. Crucially the queue is *not* parallel swim lanes — execution is sequential, so it shows progression and waiting, ordered the way drain-cycle would actually pick issues (topological sort over `blocked_by[]`).

Net shape for the design doc:
- Horizontal stepper spine: `done ● / active ◉ / upcoming ○` across the fixed `exec:*` chain (`pickup → breakdown → build → review → verify → simplify → finish`; `debug` as escalation).
- Vertical drill-down under the active step only, showing its persona fan-out with per-persona state and verdict where one exists (e.g. `spec-compliance ✓ GO`).
- Active node shows a one-line sub-status for liveness.
- Status-only, redraws in place; coexists with `-w` (which keeps raw streaming) rather than duplicating it.
- **Cycle queue above the swimlane**: issues in dependency-resolved execution order (topo sort over `blocked_by[]`), each with lane state and a dependency note; a focus marker selects which issue's swimlane is drawn.
- **Viewing toggle**: select any issue (running, done, or queued) without halting the run; auto-follow the running issue by default, manual selection suspends follow until re-armed. Viewing only — never reorders or parallelises execution.

## Carry-forward into Track A
- **Feasibility is still the unvalidated assumption.** This spike resolved the *layout*, not whether drain-cycle can observe step/persona transitions. The design doc's first open question (with an owner and a resolution gate before any build slice) must be: can the orchestrator derive the active step + active persona from the worker's existing event stream, or is a lightweight marker contract required? See `drain_cycle/worker.py`, `console.py`, `progress.py`.
- **Redraw-in-place needs an operability/terminal-capability decision.** Redrawing a fixed region (vs append-only) interacts with non-TTY output, piping (stdout is kept clean today — see `console.py`), and CI/log capture. The design doc must state the fallback when stdout is not a TTY. The prototype demonstrates one: a non-interactive play-through that auto-follows the running issue.
- **The queue needs the cycle's dependency-resolved execution order.** Rendering the queue assumes the orchestrator exposes the ordered issue list (topologically sorted over `blocked_by[]`) and per-issue lane state (done/running/queued). Confirm this is already available from the run plan / `active.json`, or name what must surface it. See `drain_cycle/progress.py` (`active.json`), the startup plan in `console.py`.
- **The viewing toggle adds an input surface, not just an output one.** Keyboard selection over a live-redrawing region needs raw-mode TTY handling with a graceful non-TTY fallback (no input → auto-follow). This is a new operability concern beyond redraw-in-place: it must degrade cleanly when piped/in CI, and the toggle must never block or perturb the run itself (viewing-only).
- The chain rendered must be the *real* `exec:*` chain and personas, not an invented one (`docs/architecture.html`).

## Disposition
The narrative-mode mockups in this finding are illustrations, explicitly non-production. The follow-up iteration produced throwaway code — `docs/prototypes/live-execution-swimlanes/demo.py` — which is marked NON-PRODUCTION in its module docstring (per Track C, C6): it simulates timings and invokes no `claude`, reading the real pack only for captions/personas. It is kept (not deleted) as a runnable illustration of the layout in motion for the design-doc reader, and must not be extended toward production — that requires the design doc and a clean implementation.

## Next step
Run `shape:design` Track A on this finding to produce `docs/design-docs/live-execution-swimlanes/design-doc.md`, opening with the feasibility open question above as a gated unknown.
