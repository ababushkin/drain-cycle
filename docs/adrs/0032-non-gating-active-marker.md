# ADR 0032: The `_active` step/persona marker is read for display only

**Date:** 2026-06-21
**Status:** Accepted
**Plan-review:** APPROVE — one-way-door boundary rule on shared infrastructure (the supervisor↔pack contract, ADR 0002 / 0030). Gated on: (1) carrier choice settled against ADR 0030; (2) schema-compat verdict recorded against the N01 grader (`drain_cycle/kr2_check.py`) and the `exec-state.json` reader (`drain_cycle/handoff.py`); (3) enforcement named as NFR-3 fault-injection + an import-guard test; (4) display-only invariant stated explicitly. All four conditions met in the sections below.

During a drain run, the live-execution swimlanes show which `exec:*` step is executing and which review persona is active. The artifact boundary withholds exactly that detail from Layer 1 ([`architecture.html`](../architecture.html) §5; [ADR 0002](0002-thin-supervisor-contract.md)): the supervisor reads only *whether* an artifact exists and a handful of gating fields (`verify.verdict`, `finish.pr_urls`), never what a phase did inside the phase. This ADR settles how to show inside-phase content on screen without letting it decide anything.

**Decision.** The active marker is a **non-gating, display-only signal**. Layer 1 may render it; no decision path may read it. No advancement, halt, grade, retry, exit-code, or stop-guard decision may import the marker reader. The marker is to the swimlane what a run-log timestamp is to the run record: Layer-2-authored content the supervisor shows but never decides on.

The pattern — *display-only reads across the artifact boundary* — is reusable. Any future feature that needs to show inside-phase detail on the operator's screen (a per-step elapsed clock, a sub-task counter, a persona attempt index) inherits the same invariant: Layer 1 may render it, no decision path may read it.

## Carrier (settles OQ-3)

The marker is a top-level `_active` pointer in the pack-owned `exec-state.json`, not a field on the supervisor-written `~/.drain-cycle/active.json`.

```json
{
  "_active": {"step": "review", "persona": "code-quality"},
  "pickup":  { … },
  "build":   { … }
}
```

**Why `exec-state.json`, not `active.json`.** A skill writing the marker is "what a role does" — the placement test in [architecture.html](../architecture.html) §5 routes that content to the pack ([ADR 0030](0030-execution-state-file.md)). The supervisor writes `active.json` (per-turn token usage and timing, [ADR 0015](0015-active-run-marker.md)); making the pack write into it would invert ownership and force the supervisor to read the marker out of its own file before the pack writes it — the supervisor coupling [ADR 0030](0030-execution-state-file.md) removed. Putting the marker alongside the phase-section writes the skill already makes keeps one writer per file and one execution-state artifact per task.

**Reader and writer owner.** The pack owns the marker:

- *Writer*: the currently executing `exec:*` skill, on step or persona entry. The next skill's entry overwrites `_active` to record the exit; `exec:finish` clears it at run end.
- *Reader*: the supervisor's renderer (Layer 1, display path only). The pack's own skills do not read it — they know which step they are in by running.

**Why a top-level `_active`, not a phase section.** Each phase section (`pickup`, `breakdown`, `build`, `review`, `verify`, `finish`) holds that phase's *completed* output ([ADR 0030](0030-execution-state-file.md) — "the phase's natural output is its latest state, not a log of attempts"). `_active` is the opposite: a transient pointer to the *currently executing* step. Keeping it at the top level, with an underscore prefix to signal "not a phase section," preserves the phase-keyed schema the supervisor reads while adding the pointer the renderer needs. The two never collide: no skill writes a phase section named `_active`, and no decision path reads outside the named phase keys.

**Schema-compat verdict (N01-grader).** PASS. The grader, `drain_cycle/kr2_check.py`, reads run-log entries — not `exec-state.json` — and checks that every `Done` entry carries `outcome_verdict`. The added `_active` pointer never reaches it. `drain_cycle/handoff.py:_read_exec_state` parses the JSON and reads only the `finish`, `verify`, and `review` keys via `payload.get(...)`; any other top-level key, including `_active`, is silently ignored. The pointer is therefore additive and backward-compatible with both readers: an older supervisor reads the file untouched, and the grader's contract is unaffected.

## Last-writer rule (settles OQ-2)

`_active.persona` is a **single string**, written by atomic rename (temp-file + rename, the pattern [ADR 0015](0015-active-run-marker.md) requires of `active.json`), with **last write wins**. A parallel persona fan-out (Claude Code sub-agents inside `exec:review`) may race; the renderer shows whichever persona's enter-write landed most recently, and a brief overlap is accepted.

We considered a *set* of personas and rejected it: adding and removing members needs read-modify-write, which races more than the single-string atomic rename. Showing "every persona currently in flight" is not worth the extra concurrency surface against a failure limit already declared cosmetic (design-doc NFR-3). A wrong persona shown for under one render tick is a cosmetic glitch by construction; missing a persona because a concurrent writer clobbered the set update would be the same glitch with more code.

## Enforcement (the invariant's fitness functions)

Two mechanical checks together prove the marker stays non-gating:

1. **NFR-3 fault-injection test.** A test raises inside the renderer mid-run and asserts the `WorkerResult` and the worker process exit code are byte-identical to a clean run. A render-path fault changes execution by zero. This is NFR-3 from the live-execution-swimlanes design doc (`docs/design-docs/live-execution-swimlanes/design-doc.md`), reused here as run-time enforcement of the display-only invariant: if anything decision-shaped depended on the marker, breaking the renderer would change the outcome.

2. **Import-guard test.** A static check greps `drain_cycle/` for imports of the marker-reader function and allows only the renderer module to import it. The reader lives in one module owned by the renderer; advancement, halt, grade, retry, exit-code, and stop-guard modules import it nowhere. A new importer is a boundary violation, caught at review before merge.

The static check stops a decision path from forming at write time; the fault-injection test catches one that slips past the static check at run time.

## Alternatives considered

- *Marker on `active.json`.* Rejected. The supervisor writes `active.json` ([ADR 0015](0015-active-run-marker.md)); making the pack co-write it inverts the ownership rule [ADR 0030](0030-execution-state-file.md) settled and adds a second writer to a file whose atomic-rename invariant is held by one. The extra concurrency buys no architectural gain.
- *Renderer reads whatever it needs ad hoc (no contract).* Rejected. Reading inside-phase content opportunistically grows the supervisor's knowledge of phases until the worker-agnostic property quietly disappears — by then there is no single line to revert.
- *`_active` as a phase section.* Rejected. Phase sections hold completed-phase outputs ([ADR 0030](0030-execution-state-file.md)); a transient pointer that overwrites every transition is the opposite shape. A top-level pointer, underscore-prefixed, keeps the phase keys clean.
- *`_active.persona` as a set with merge.* Rejected. Read-modify-write races more than atomic rename; the cosmetic limit means the extra concurrency surface buys nothing.

## Consequences

**Positive.**

- The swimlanes feature can show the live step and persona without the supervisor reading inside-phase content for any decision.
- A reusable pattern — display-only reads across the boundary — is now available for future inside-phase-display features.
- The pack owns its in-flight pointer the way it owns its completed sections; no new writer joins `active.json`.

**Negative.**

- A skill that forgets to clear `_active` leaves a stale pointer (cosmetic; bounded by NFR-3 and the design-doc's staleness warning).
- Parallel persona writes can briefly show the wrong persona (last-writer rule; cosmetic).
- Both enforcement tests must stay live; deleting either reopens the invariant.

**Relationship to ADR 0030.** [ADR 0030](0030-execution-state-file.md) settled who writes and reads the phase sections of `exec-state.json`. This ADR adds one non-section key, `_active`, with its own writer (the executing skill) and its own reader (the renderer). The phase-key roster ADR 0030 names is unchanged, and the supervisor still reads only the gating fields of the phase sections. `_active` is a named exception: the supervisor reads it for display only, through a code path that imports no decision module.
