# Spike recommendation: live-execution-swimlanes — step/persona observability

**Track:** B — backend spike
**Upstream:** `docs/ideas/live-execution-swimlanes.md` (feasibility assumption); `docs/prototypes/live-execution-swimlanes/finding.md` (layout resolved, Proceed)
**Downstream:** `shape:design` Track A design doc — this spike answers its gated first open question.

## Question
Can drain-cycle derive the active `exec:*` step and the active review persona from the worker's existing `stream-json` event stream, or must a lightweight marker contract be added?

## Failure example
During a real drain run the operator watches the default (non-`-w`) output and cannot tell the run is in `review / security-auditor` rather than `build`. The active step and persona are unknowable from what drain-cycle consumes today: the orchestrator's `_UsageAccumulator` reads only `message.id` and the four `message.usage` token fields, never `message.content` (`drain_cycle/worker.py:510–528`). The swimlanes view cannot render an active node without a signal that does not currently reach the orchestrator.

## Time-box
4 investigation steps, set before starting; not extended.
1. What the worker stream carries vs. what the orchestrator consumes.
2. How the pack invokes steps and personas (is each transition a machine-detectable boundary?).
3. What existing state files already record about the active step.
4. Synthesise.

Kill condition (set up front): if step 1 showed the stream opaque *and* the pack offered no invocation signal, the answer was "marker contract required, stop." Neither held — the box ran to synthesis. One evidence gap remains open (see Confidence): no *live* stream from a real drain run was captured; findings rest on the parsing code, the pack's SKILL/ADR prose, and the test fixtures that fix the event shapes.

## Findings (evidence)

**The stream already carries step/persona signal; the orchestrator discards it.**
- The raw `stream-json` `assistant` events carry `message.content[]` including `tool_use` blocks with `name` and `input` (`tests/test_watch_format.py:70–74`).
- The accounting path (`_UsageAccumulator.feed`, `worker.py:510–528`) reads only `message.id` + `message.usage`; it never touches `message.content`. `system` and `user` events are ignored entirely.
- `watch_format.py:100–107` *already* parses `tool_use` name + args for the `-w` display — so the parse is proven, just not wired into the accounting/default path.

**The pack's step transitions are machine-detectable; persona transitions are platform-dependent.**
- `exec:pickup` "delegates each step — breakdown, build, review, verify, finish — to its owning `exec:*` skill by name. It inlines no procedure; the named delegation is the whole of each step" (exec-pickup SKILL.md:14–16, 63–82). Each step is a *named invocation* of a discrete top-level skill (exec-build / exec-review / exec-verify all exist standalone) → a tool-use boundary an observer would see.
- Personas (ADR 0003; exec-review SKILL.md:51): on **Claude Code**, dispatched via the `Agent` tool, one call per persona (batched parallel) → three visible tool-use boundaries. On **non-Claude workers** (codex/kimi), personas run **inline-sequentially** inside one turn → **no machine-detectable boundary**. Reporting order is fixed (spec-compliance → security-auditor → code-quality) regardless.

**No existing state field names the active step.**
- `active.json` (`progress.py`) holds issue metadata + timing + tokens; no step/phase field. Written by the orchestrator for `drain-cycle status`; the orchestrator never reads it back mid-run.
- `exec-state.json` (`handoff.py`) has sections keyed by step name (`pickup`/`breakdown`/`build`/`review`/`verify`/`finish`), written section-by-section **by the skills** as each completes. There is no "current step" field — but *section presence* is an implicit last-completed-step signal already on disk. The orchestrator reads it only post-worker (verdicts, `pr_urls`), never during.

## Options

**Option A — Parse the existing stream's `tool_use` blocks.**
Extend the orchestrator (or share `watch_format`'s parser) to read `message.content`, map `Skill`/`Agent` tool-use to the active step, and map the three persona `Agent` calls to persona depth.
- Applied to the failure example: when the worker invokes `exec:review`, a `tool_use` appears; the renderer flips the spine to `review` and shows three persona sub-nodes from the batched `Agent` calls.
- Failure mode: persona depth is **invisible on non-Claude workers** (inline personas emit no tool boundary), and step detection couples the UI to Claude Code's internal tool taxonomy and skill names — a rename or a worker swap silently blanks the active node. Unverified against a live stream.

**Option B — Marker contract: skills write the active step/persona.**
Each `exec:*` skill writes a small `_active` pointer (e.g. `{"step": "review", "persona": "security-auditor"}`) into `active.json` (or an `exec-state.json` pointer field) as it begins; the orchestrator file-watches and the renderer reads it.
- Applied to the failure example: `exec:review` sets `step=review`; each persona (sub-agent on Claude Code, inline loop on codex) sets `persona=…` before working → the renderer shows `review / security-auditor` on every worker.
- Failure mode: requires editing every `exec:*` skill in the **separate** `agent-skills-shaper` repo (cross-repo coordination); a skill that forgets to update or clear the pointer leaves a stale active node. Persona writes from parallel Claude-Code sub-agents need a defined last-writer/merge rule.

**Option C — Derive last-completed step from `exec-state.json` section presence.**
File-watch the worktree's `exec-state.json`; infer the active step as "the one after the last populated section."
- Applied to the failure example: once `build` is populated and `review` is not, infer active = `review`.
- Failure mode: **no persona granularity at all** (the `review` section is written once, at the end), and it lags by one — it reports the last *completed* step, not the currently *running* one, so a stuck/long step shows the prior step as active. Fails the trust-at-a-glance outcome precisely when the run is slow.

**Option D — Do nothing (keep the flat output).**
- Failure mode: the run stays a black box; the upstream trust outcome is unmet. Listed for completeness.

## Scope check (adjacent paths)
- **`-w` watch pane / `watch_format.py`** — already parses `tool_use`. **In scope** as the shared parser: any stream-derived step detection must reuse it, not duplicate it. (Related: ABA-386–390 watch upgrade.)
- **`drain-cycle status` / `active.json`** — the marker's natural carrier and reader. **In scope.**
- **`agent-skills-shaper` exec:* skills** — the marker writers under Option B. **Out of this repo's scope** (separate repo); named as a required cross-repo follow-up, co-located with the existing section writes.
- **`exec-state.json` schema / N01 grader** — adding an `_active` pointer must stay backward-compatible with the grader and `handoff.read*`. **In scope** for the design doc's schema review.

## Confidence, semantics, failure ceiling (leading option = B)
- **Confidence (Gilad): 4 — strong for step-depth, moderate for persona-depth.** Step-depth feasibility rests on two independent mechanisms (stream tool-use + on-disk section presence) plus a proven parser → high. The marker contract itself is a small, well-located write. The moderate cap is the one open gap: **no live `stream-json` from a real drain run was captured**, so the exact tool-use shape of a skill delegation and of the batched persona `Agent` calls is inferred from prose + fixtures, not observed. Resolving evidence: capture one real run's stream and grep for the `Skill`/`Agent` tool-use names — a single pre-build step.
- **Semantics delta: none.** Purely additive observability. An `_active` pointer in `active.json` touches nothing downstream; an `exec-state.json` pointer field is backward-compatible if the grader/`handoff` reads ignore unknown keys (confirm in the design doc).
- **Failure ceiling: cosmetic.** Worst case the swimlane shows a stale or blank active node / misattributed persona. It must never affect execution — the observability path is read-only w.r.t. the worker's work, consistent with correctness > throughput. The design doc states this as a hard invariant.

## Recommendation
**Adopt Option B (marker contract), built on a stream-derived walking skeleton.**

Option B is the only choice that delivers **persona depth on every worker** (the part the layout most needs) and **decouples the UI from Claude Code stream internals** — both required by the trust outcome and the project's worker-agnostic, correctness-first direction. Its cost is cross-repo coordination, which is contained because the skills already write `exec-state.json` section-by-section; the pointer write sits next to those.

Sequence the build so risk is retired cheaply:
1. **Walking skeleton (this repo only, no pack change):** wire the renderer to **step-depth from the existing stream `tool_use`** (Option A mechanism, reusing `watch_format`'s parser). Proves the spine + redraw-in-place end-to-end with zero cross-repo work. *Gate: first capture one real stream and confirm the step tool-use shape (closes the Confidence gap).*
2. **Persona depth + robustness:** add the `_active` step/persona pointer to the `exec:*` skills and switch the renderer to read it, making the view worker-agnostic and rename-proof. The skeleton's stream path becomes a fallback for environments without the marker.

### Rejected alternatives
| Option | Rejected because (specific failure mode) |
|---|---|
| **A — stream `tool_use` only** | Persona depth is unobservable on non-Claude workers (inline personas emit no tool boundary), and step detection couples the UI to Claude Code tool names/skill names — a worker swap or rename silently blanks the active node. Kept only as the skeleton + fallback, not the contract. |
| **C — section-presence inference** | No persona granularity (the `review` section is written once, at completion) and it lags by one step, so a slow/stuck step displays the *previous* step as active — it fails exactly when trust matters most. |
| **D — do nothing** | Leaves the run a black box; the upstream trust outcome is unmet. |

A and B are not indistinguishable: A is strictly weaker on the persona axis and on worker-agnosticism, which is why B leads and A is demoted to skeleton/fallback rather than co-recommended.

## Follow-up implementation ticket
**Build:** an `_active` step/persona marker written by the `exec:*` chain, surfaced through `active.json`, plus the swimlanes renderer that consumes it (with the stream-`tool_use` skeleton as fallback).
**Approach:** Option B above, in the two slices listed.
**Acceptance criterion (references the failure example):** during a real drain run, the default output's swimlane shows the correct active step within one step of ground truth, and the correct active persona during `review`, on **both** a Claude Code worker and a non-Claude (e.g. codex) worker — i.e. the operator can read `review / security-auditor` at the moment it is true, which is impossible today.
**Pre-build gate:** capture one real `stream-json` and confirm the step/persona `tool_use` shape, closing the Confidence-4 evidence gap before slice 1.
**Cross-repo:** the marker-write change lands in `agent-skills-shaper` (separate repo), co-located with each skill's existing `exec-state.json` section write; the design doc owns the schema-compatibility review with the N01 grader.

## Disposition
No throwaway code produced (investigation only). This recommendation feeds the Track A design doc as the resolution of its first gated open question.
