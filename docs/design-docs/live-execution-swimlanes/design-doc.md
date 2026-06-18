# Design doc: live-execution-swimlanes

**Date:** 2026-06-18
**Status:** Draft — not accepted; no implementation until accepted.
**Track:** A — design document (`shape:design`)
**Trigger:** Shared-interface surface — the feature reads across the artifact boundary (architecture §5) and touches the supervisor↔pack contract (ADR 0002, 0030), so it cannot be a purely additive local change.

**Upstream:**
- `docs/ideas/live-execution-swimlanes.md` — triage record (Confidence 0.5 → validation slot).
- `docs/prototypes/live-execution-swimlanes/finding.md` — product spike, **Proceed**; resolved the layout.
- `docs/spikes/live-execution-swimlanes-observability/recommendation.md` — backend spike; resolved feasibility (Option B, Gilad 4).

**Related decisions:** ADR 0002 (thin-supervisor contract / artifact boundary), ADR 0008 (per-run log), ADR 0015 (`active.json` marker), ADR 0017 (opt-in OTel), ADR 0019 (`-w` runs claude in the tmux pane), ADR 0030 (pack-owned execution-state file). Architecture §5 (Layer 2 writes signals; Layer 1 reads them), §7 (state plane).

---

## Problem

The drain-cycle operator runs a cycle and steps away — that is the point of the supervisor (vision: "my attention comes off the mechanical loop"). To step away, the operator must trust the run is following the standard `exec:*` chain. Trust requires seeing *where in the chain* the work is.

**Affected user.** The operator running a drain-cycle in the foreground (the default, non-`-w` invocation), who wants to glance at the terminal and know the run is healthy without reading it line by line.

**Current behaviour.** The default output is an append-only event stream: orchestrator lines (`orch`), per-issue lines (`ABA-NNN`), halt lines, and `│`-indented passthrough of the worker's text, plus a single progress line of turns/tokens/elapsed (`console.py`, `progress.py:format_progress_line`). None of it names the active `exec:*` step or the active review persona. The supervisor itself does not hold that information: it consumes the worker stream only for token accounting (`worker.py:_UsageAccumulator.feed` reads `message.id` + `message.usage`, never `message.content`). No state file records a current step: `active.json` has timing/tokens but no phase field, and `exec-state.json` sections appear only *after* each phase completes.

**Desired behaviour.** At a glance during a live run, the operator can tell which step is executing, which review persona is active, and what is done versus upcoming — both for the issue in flight and across the whole cycle's queue in dependency-resolved execution order.

**Why now.** The flat stream makes a multi-minute run read as a black box exactly when the operator wants to look away. The product spike confirmed a layout that delivers trust-at-a-glance; the backend spike confirmed the signal can be obtained. The remaining unknown is structural: how to obtain and render it *without breaking the artifact boundary that keeps the supervisor vendor-agnostic*.

---

## Context and constraints

### Inherited architecture (what must not break)

The supervisor is split from the workflow on the **artifact boundary** (architecture §5; ADR 0002): Layer 2 (the `exec:*` skills) does the work and writes signal artifacts; Layer 1 (drain-cycle) reads only *whether* an artifact exists and a handful of *gating* fields (`verify.verdict`, `finish.pr_urls`). Layer 1 never reads what a phase did *inside* the phase, and never advances or halts on inside-phase detail. This is what keeps the supervisor worker-agnostic (Sonnet today, codex/kimi tomorrow — ADR 0011) and keeps "done means done" enforceable from artifacts alone (the stop-guard, architecture sguard).

Step/persona observability reads precisely the inside-phase detail the boundary withholds. **The design's central obligation is to surface that detail for display without letting it gate anything** — advancement, halt, grading, or the worker's exit code. Layer 1 may *display* Layer-2-authored content; it may not *decide* on inside-phase content.

### Inherited surfaces

- **Output** (`console.py`): all run output goes to **stderr**, append-only, no live-refresh region; **stdout is kept clean for piping**. Startup plan (`startup_plan`) already prints the cycle's issue list at run start. Worker text is wrapped through `AgentSink` → `agent_line`.
- **Active marker** (`progress.py`): `~/.drain-cycle/active.json`, written per-turn by the orchestrator, read by `drain-cycle status`. No step/phase field.
- **Execution state** (`handoff.py`): `exec-state.json`, pack-owned (ADR 0030), one section per phase written by each skill as it completes; supervisor reads only `finish.pr_urls` + `verify`; **reads already ignore unknown keys** (backward-compatible).
- **Stream parse** (`watch_format.py`): `StreamFormatter._feed_assistant` already parses `tool_use` `name`/`input` for the `-w` pane — proven parse, but formats-to-string only; no reusable extractor exposes the parsed structure.
- **Watch pane** (ADR 0019): `-w` is a *separate* surface — raw streaming in a tmux pane. Swimlanes is the non-`-w` default and must not duplicate it.
- **Execution order** (ADR 0016): order is manual drag-order, blocks-aware. The cycle queue must render the same order the orchestrator actually picks — a topological sort over `blocked_by[]`, tie-broken by drag-order — sourced from the orchestrator, not recomputed by the renderer from a different rule.

### Non-functional requirements

| # | NFR | Number + unit | Fitness function |
|---|---|---|---|
| NFR-1 | **Liveness accuracy** — the rendered active node matches ground truth | within **1 step transition** of truth; marker-to-render staleness **≤ 1 s** | Test drives a synthetic stream/marker sequence and asserts the rendered active node is correct within one transition; staleness asserted against a fake clock. |
| NFR-2 | **Zero worker overhead** — observability must not alter the worker's execution or token usage | **0** added `claude` invocations on the render path; worker token usage **unchanged** (±0) with swimlanes on vs off | Test asserts the render path issues no subprocess; a golden-token test asserts identical `_UsageAccumulator` totals with the feature on and off. |
| NFR-3 | **Non-gating / failure ceiling** — a render-path fault never affects execution | render-path exception changes worker exit code and run outcome by **0** | Fault-injection test raises inside the renderer mid-run and asserts `WorkerResult` and exit code are identical to a clean run. |
| NFR-4 | **Clean degradation** — output stays correct when not a TTY | **0** ANSI control sequences on non-TTY stderr; **0** bytes on stdout | Test pipes stderr (non-TTY) and asserts no escape sequences; asserts stdout byte count is 0. |
| NFR-5 | **Marker write cost** — the pack's marker write is negligible | **≤ 1** small file write (**< 1 KB**) per step/persona transition; no added network calls | Static check on the skill change + a write-count assertion in the pack's contract test. |
| NFR-6 | **Worker-agnostic coverage** — persona depth shows on non-Claude workers | active persona renders correctly on **both** a Claude Code worker and **≥ 1** non-Claude worker (codex) | Contract test feeds a Claude-Code stream fixture and a codex fixture; both yield the correct active persona via the marker path. |

"Fast" and "responsive" are not requirements here; NFR-1 fixes the only latency that matters (a stale active node erodes the trust the feature exists to build).

---

## Alternatives

The backend spike enumerated and stress-tested all four; each is summarised here with blast radius and reversal cost.

### Alt 1 — Do nothing (keep the flat stream)
Leave the default output as the append-only event stream.
- **Blast radius if wrong:** none technically, but the upstream trust outcome stays unmet — the run remains a black box during the window the operator most wants to look away.
- **Reversal cost:** zero (it is the status quo).
- **Rejected because:** it is the problem, restated.

### Alt 2 — Parse the existing stream's `tool_use` only (spike Option A)
Extend the orchestrator to read `message.content`, mapping `Skill`/`Agent` tool-use to active step and the three persona `Agent` calls to persona depth, reusing `watch_format`'s parser.
- **Blast radius if wrong:** persona depth is **invisible on non-Claude workers** (inline personas emit no tool boundary), and step detection couples the UI to Claude Code's tool taxonomy and the pack's skill names — a worker swap or a rename silently blanks the active node. Violates the worker-agnostic spirit of ADR 0011.
- **Reversal cost:** low — it is this-repo-only and additive.
- **Verdict:** kept as the **walking skeleton and fallback**, not the contract (see Recommendation).

### Alt 3 — Marker contract: skills write the active step/persona (spike Option B)
Each `exec:*` skill writes a small `_active` pointer (`{"step": …, "persona": …}`) as it begins a step/persona; the orchestrator file-watches it and the renderer reads it.
- **Blast radius if wrong:** a skill that forgets to update/clear the pointer leaves a stale active node (cosmetic, bounded by NFR-3). Requires a cross-repo change in `agent-skills-shaper`. Parallel persona writes (Claude Code sub-agents) need a defined last-writer rule.
- **Reversal cost:** moderate — the marker is additive and ignorable; reverting means the renderer falls back to the skeleton (Alt 2) or to flat output. The pack write is one section next to the writes ADR 0030 already mandates.
- **Verdict:** **recommended** — the only option that gives persona depth on every worker and keeps the UI decoupled from stream internals.

### Alt 4 — Infer last-completed step from `exec-state.json` section presence (spike Option C)
File-watch `exec-state.json`; infer active step as "the one after the last populated section."
- **Blast radius if wrong:** **no persona granularity** (the `review` section is written once, at completion) and it **lags by one step** — a slow or stuck step displays the *previous* step as active, failing NFR-1 precisely in the slow-run case the feature exists to cover.
- **Reversal cost:** low.
- **Rejected because:** structurally cannot meet NFR-1 (liveness) or persona depth; it reports completed, not current.

---

## Recommended approach

**Adopt Alt 3 (marker contract), built on an Alt 2 stream-derived walking skeleton, rendered as the status-only swimlanes layout the product spike chose.** Two slices, sequenced so the riskiest cross-repo work is retired only after the spine is proven cheaply.

### The signal: a non-gating active-marker

The pack writes an `_active` pointer as the natural, additive output of entering a step/persona — co-located with the `exec-state.json` section writes ADR 0030 already mandates. Carrier decision (active.json vs an `exec-state.json` pointer field) is an open question (OQ-3); either way the field is **non-gating by contract**:

> **Invariant (boundary reconciliation).** The active-marker is read by Layer 1 **for display only**. No advancement, halt, grade, retry, exit code, or stop-guard decision may read it. It is to the swimlane what a run-log timestamp is to the run record: Layer-2-authored content the supervisor may *show* but never *decide on*. This keeps the feature inside architecture §5 — the supervisor stays content-blind for every decision; it gains sight only for the screen.

This is the line that distinguishes "swimlanes" from "the supervisor now understands phases." It must be stated in the follow-up ADR and enforced by NFR-3's fault-injection test plus a guard test asserting no decision path imports the marker reader.

### The render: status-only swimlanes

From the product-spike finding (the layout is settled; this doc does not re-open it):
- **Cycle queue** above the swimlane: every issue in dependency-resolved execution order (topo sort over `blocked_by[]`, ADR 0016 order), each with lane state `done ● / running ◉ / queued ○` and a dependency note; a focus marker selects which issue's swimlane is drawn.
- **Horizontal stepper spine** for the issue in focus: `done ● / active ◉ / upcoming ○` across `pickup → breakdown → build → review → verify → simplify → finish` (`debug` as escalation).
- **Vertical drill-down** under the active step only, showing the persona fan-out with per-persona state and verdict where one exists.
- **One-line sub-status** on the active node for proof-of-life; **redraws in place**, status-only, no scrolling raw log (that stays `-w`'s job).
- **Viewing toggle** (input surface): select any issue without halting the run; auto-follow the running issue by default; manual selection suspends follow until re-armed. Viewing-only — never reorders or parallelises.

### Slice 1 — Walking skeleton (this repo only, no pack change)

Wire the renderer to **step-depth from the existing stream `tool_use`** (Alt 2 mechanism), reusing a parser extracted from `watch_format` (do not duplicate it; the extraction is the in-scope refactor). Renders the spine + queue + redraw-in-place + the viewing toggle end to end, against the live stream, with **zero cross-repo work**. Persona depth is best-effort here (Claude-Code-only).

**Pre-build gate (carried from the spike, OQ-1): CLEARED for step-depth.** A captured `stream-json` confirms a step delegation is a `tool_use` block `{name:"Skill", input:{skill:"<name>"}, caller:{…}}` — the active step is `input.skill`, machine-readable, no free-text parsing. Each block also carries a `caller` field (`{type:"direct"}` at top level) that discriminates a top-level delegation from a tool call made *inside* a sub-agent — a usable nesting/depth signal. The same capture shows the `Agent` block input is `{description, prompt}` with **no structured persona field**, confirming the stream is the wrong place to derive persona identity and reinforcing that persona depth must come from the marker (slice 2), not the stream. Slice 1 may proceed on step-depth.

### Slice 2 — Marker contract (persona depth + worker-agnostic)

Add the `_active` step/persona pointer to the `exec:*` skills (cross-repo, `agent-skills-shaper`) and switch the renderer to prefer it, falling back to the slice-1 stream path where no marker is present. This delivers persona depth on **every** worker (NFR-6) and makes the view rename-proof and stream-internal-independent.

### Why this beats the alternatives on the stated constraints

- Against **Alt 1**: meets the trust outcome instead of restating the problem.
- Against **Alt 2 alone**: Alt 2 cannot meet NFR-6 (persona depth dies on non-Claude workers) and couples the UI to Claude internals; demoting it to skeleton+fallback keeps its cheap end-to-end value without betting the contract on it.
- Against **Alt 4**: Alt 4 structurally fails NFR-1 (lags by one) and has no persona granularity.
- Against the **artifact boundary**: the non-gating invariant keeps every *decision* artifact-only; only the *display* gains sight. The marker is one more pack-authored section, exactly the shape ADR 0030 already blesses.

---

## Consequences

**Walking skeleton: required, and it is slice 1.** Pre-skeleton estimates of the render/redraw work are uncalibrated until the spine runs against a real stream — which is why slice 1 is explicitly skeleton-first and this-repo-only, and why the pre-build gate (capture a real stream) precedes it. The cross-repo cost (slice 2) is only paid after the spine is proven.

**Positive.**
- The default run becomes glanceable; the operator can step away — the vision's payoff.
- The boundary reconciliation (non-gating marker) is reusable: any future "show inside-phase detail" feature has a sanctioned pattern (display-only, never gates).
- Slice 1 ships value with no cross-repo coordination; slice 2's cross-repo change rides next to existing pack writes.

**Negative / costs accepted.**
- A cross-repo contract (`agent-skills-shaper`) with the usual coordination cost and a stale-pointer failure mode (bounded cosmetic by NFR-3).
- Redraw-in-place introduces terminal-state management into a codebase that is append-only today — a new operability surface (handled below).
- A new input surface (keyboard toggle) requiring raw-mode TTY handling with a non-TTY fallback.
- Persona parallel-write ordering needs a defined rule (OQ-2).

**Path to production** is not fully clear pre-skeleton — the redraw/toggle integration with the stderr append model is the unknown slice 1 retires.

---

## Operability plan

This feature *is* observability for the operator, but it is also a long-running rendering component and must itself be operable. The failure ceiling is cosmetic by construction (NFR-3), which shapes every choice below: nothing here pages, because nothing here can break a run.

- **Metrics.** (1) marker staleness — seconds since last `_active` update while a run is active; (2) render-path swallowed-exception count; (3) marker-miss count (renders that fell back from marker → stream → flat). Exposed via the existing run-log (ADR 0008) as end-of-run counters, not a new metrics system.
- **Structured logs.** Render path logs at debug; every swallowed render exception (NFR-3) is logged once at warning with the exception and the fallback taken, so a cosmetic glitch is diagnosable without being fatal.
- **Traces.** Opt-in OTel already exists (ADR 0017); add the active step/persona as span attributes on the existing worker span when tracing is on. No new exporter.
- **Alerts.** None page (cosmetic ceiling). One log-only warning: marker staleness exceeds a threshold (default 120 s) while the run is demonstrably live (tokens still advancing) — signals a skill that forgot to update/clear the pointer. Routed to the run-log, not on-call.
- **Rollback** (ordered, each with a verification gate):
  1. Set the disable switch (env var / flag, OQ-5) → **verify** the default output reverts to today's flat append-only stream (golden-output test).
  2. If slice 2 misbehaves but slice 1 is fine, the marker reader falls back to the stream path automatically → **verify** persona depth degrades to Claude-only, step depth intact.
  3. Pack-side: a flag gating the marker write is unnecessary — an older pack simply omits the field and the renderer falls back → **verify** with an old-pack fixture (the marker-miss path, NFR-6 fallback leg).
- **Capacity headroom.** One sub-1 KB file per worktree, written on transitions only (NFR-5); file-watch/poll at ≤ 1 Hz (NFR-1). Negligible against existing per-turn `active.json` writes. No new process, no network.
- **Known failure modes → mitigations.**
  - *Stale marker* (skill forgot to clear) → staleness warning + render shows last-known with a dimmed/aged active node; never blocks. (NFR-3)
  - *Missing marker* (old pack / non-cooperating worker) → fall back to stream skeleton, then to flat output. (NFR-6)
  - *Parallel persona write race* (Claude-Code sub-agents) → last-writer rule (OQ-2); worst case a wrong persona shown briefly, cosmetic.
  - *Terminal resize / non-TTY / pipe* → redraw guarded by TTY check; non-TTY degrades to append-only with no control sequences and clean stdout (NFR-4).
  - *Worker swap / skill rename* (Alt 2 path only) → blanks the active node on the stream path; the marker path (slice 2) is immune, which is the reason it is the contract.
- **Upstream/downstream dependency failure modes.**
  - *Pack does not write the marker* (upstream, `agent-skills-shaper`) → renderer falls back; no run impact.
  - *`exec-state.json` schema / N01 grader* (downstream) → if the marker rides in `exec-state.json`, the added field must stay backward-compatible; handoff reads already ignore unknown keys, and the grader must too — verified in the schema-compat review (OQ-3).
  - *tmux/`-w` path* (sibling) → unaffected; swimlanes is the non-`-w` surface and shares no terminal state with the pane (ADR 0019).

---

## Open questions

Each carries an owner and a resolution gate (the slice blocked until it is answered).

| # | Question | Owner | Resolution gate |
|---|---|---|---|
| OQ-1 | Confirm the real `Skill`/`Agent` `tool_use` shape from a captured live `stream-json` (closes the spike's Gilad-4 evidence gap). | Anton | **CLEARED for step-depth** (see slice 1 gate): `Skill` block carries the step in `input.skill`; `caller` gives nesting. Residual: a *real* `exec:review` persona dispatch was not captured — but the synthetic shows `Agent` input has no structured persona field, so persona-from-stream is best-effort only and the contract puts persona on the marker (slice 2). The real-review fixture is captured during slice 2 (Path B), not as a slice-1 blocker. |
| OQ-2 | Last-writer / merge rule for parallel persona writes from Claude-Code sub-agents (one `_active.persona` vs a set). | Pack author (agent-skills-shaper) + this design | **Before slice 2** — the marker schema must define it before the skills write it. |
| OQ-3 | Marker carrier: a new `active.json` field (supervisor-written, but then the pack can't author it) vs an `exec-state.json` `_active` pointer (pack-authored, needs N01-grader schema-compat). Resolve the ownership tension against ADR 0030. | This design + N01 owner | **Before slice 2** — schema-compat review with the grader is the gate. |
| OQ-4 | Redraw-in-place mechanism and its coexistence with the stderr append model and `AgentSink` passthrough: does the default output replace the scrolling worker text with the status region, or keep a tail? (Library: `rich.Live` vs hand-rolled ANSI.) | This design + impl | **During slice 1** — the skeleton must demonstrate the chosen mechanism end to end. |
| OQ-5 | Disable switch shape (env var vs flag vs config) and default (on/off for the first release). | Anton | **Before slice 1 lands** — rollback step 1 depends on it. |
| OQ-6 | Does the cycle queue's execution order come from an existing orchestrator structure, or must one be surfaced? (ADR 0016 order + per-issue lane state.) | This design + impl | **During slice 1** — the queue pane needs the ordered list as input. |

---

## Disposition / next step

On acceptance, this doc emits a follow-up delivery node set (two slices above) under the existing delivery-plan structure, with the non-gating invariant captured as a short ADR (display-only reads across the artifact boundary). No implementation begins until: (1) this doc is accepted, and (2) OQ-1's pre-build gate is cleared — now done for slice 1 (step-depth shape confirmed from a captured stream; persona evidence deferred to slice 2).
