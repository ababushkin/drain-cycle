# ADR 0032: The `_active` step/persona marker is read for display only

**Date:** 2026-06-21
**Status:** Accepted
**Amended:** 2026-06-23 — the single-string `_active.persona` is joined by a display-only per-persona roster and per-name verdict keys; see [Amendment](#amendment-2026-06-23-per-persona-display-only-roster-and-verdicts).
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

The [2026-06-23 amendment](#amendment-2026-06-23-per-persona-display-only-roster-and-verdicts) splits the rejected "set" in two: a mutable shared set each writer edits in place stays rejected (read-modify-write races), but a write-once roster paired with per-persona completion keys each writer owns alone is accepted there.

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

## Amendment (2026-06-23): per-persona display-only roster and verdicts

**Status:** Accepted. **Plan-review:** APPROVE after one REVISE round — the gate rejected an earlier positional derivation rule (deriving "done" from a persona's position in the roster), which mislabels state under the parallel fan-out this serves; the derivation below reads each persona's own completion key instead. Conditions and how each is met are listed at the close of this amendment. One-way-door reversal on the same shared boundary the original decision governs (the supervisor↔pack contract, [ADR 0030](0030-execution-state-file.md); [architecture.html](../architecture.html) §5).

**What reverses, and what does not.** The [Last-writer rule](#last-writer-rule-settles-oq-2) above rejected any *set* of personas because adding and removing members needs read-modify-write. The rejection conflated two shapes. A **mutable shared collection** each sub-agent edits in place stays rejected for exactly the reason given — it races. A **write-once roster plus per-persona completion keys** each writer owns alone does not need that read-modify-write, and this amendment accepts it. The persona fan-out (the drill-down node that depends on this amendment) needs per-persona state — which persona has cleared, which is running, which still waits — and one string cannot carry it. The roster and the completion keys carry it without the contended update.

**New shape.** Alongside the unchanged single `_active.persona`, the pack may author two additive keys:

```json
{
  "_active": {
    "step": "review",
    "persona": "code-quality",
    "persona_roster": ["spec-compliance", "security-auditor", "code-quality"]
  },
  "review": {
    "verdict": "GO",
    "findings": [],
    "persona_verdicts": {
      "spec-compliance":  {"verdict": "GO"},
      "security-auditor": {"verdict": "GO"}
    }
  }
}
```

- `_active.persona_roster` — an ordered list of persona names. `exec:review` authors it **once** on entry from its static persona list, giving the renderer the full membership and a stable display order. It is never mutated member-by-member.
- `review.persona_verdicts.<name>` — each persona's own completion key, written once when that persona finishes, under its **own name**, carrying its `GO`/`NO-GO`. Its presence is the persona's done signal; its value is the ✓/✗ mark.
- `_active.persona` is unchanged: a single string, atomic rename, last-write-wins, marking the one **running** persona.

**Write rule — no writer reads another's value.** The renderer derives each persona's state from the roster plus two signals each persona owns alone, never from the position of another persona:

- **done** — the persona has a `review.persona_verdicts.<name>` entry (it finished and wrote its own key); ✓ on `GO`, ✗ on `NO-GO`;
- **running** — the persona equal to `_active.persona`, with no entry yet;
- **queued** — any other roster member.

The roster's order drives display order only; no persona's state is inferred from where another sits in the list. The roster is static and authored once. The running marker is the single atomic string this ADR already governs. Each completion key sits under its own writer's name. The **falsifier** holds: if any write needs to read a key another writer owns to compute its value, the shape is wrong — the mutable shared `personas[]` array (each sub-agent reading the array, finding its slot, writing it back) is that wrong shape, and stays rejected.

**The residual race stays cosmetic — but is not one-tick-bounded for verdicts.** `exec-state.json` is one JSON document; every write rewrites the whole file by atomic rename, so two concurrent persona writers still clobber each other at the file level — the later write drops the earlier's just-added completion key. Two losses follow, with different durations, both cosmetic. A clobbered `_active.persona` self-heals on the next enter-write, so a wrong **running** highlight lasts under a render tick (the limit the [Last-writer rule](#last-writer-rule-settles-oq-2) already accepts). A clobbered `persona_verdicts` key does **not** self-heal — a persona writes it once, at completion, so a dropped verdict stays missing until any later file write happens to re-serialize the tree, possibly to end of run. The affected persona then shows **running** or **queued** with no ✓/✗ for the rest of the run. Both are missing marks, never wrong ones, and neither corrupts another persona's data, because no write depends on reading another's — a glitch bounded by NFR-3, never an execution effect. A missing checkmark on a completed review is the accepted price; the run's GO/NO-GO outcome lives in `review.verdict`, which no persona key feeds.

**Still display-only.** Every read of `persona_roster` and `persona_verdicts` stays non-gating. No advancement, halt, grade, retry, exit-code, or stop-guard path may read either key. The placement test ([architecture.html](../architecture.html) §5) routes a roster `exec:review` produces to the pack: it is *what a role produces* — Layer 2 content, shown by Layer 1, decided on by neither.

**Backward-compatible.** `drain_cycle/handoff.py` reads only the `finish`, `verify`, and `review` sections, and within `review` only `verdict` and `findings`, all via `.get(...)`. `_active.persona_roster` and `review.persona_verdicts` reach no reader and are ignored — additive, exactly as `_active` itself was (see the Schema-compat verdict above). An old pack that writes no roster degrades to the single inline `_active.persona`: the renderer finds no roster and shows only the running persona.

**Enforcement — the same two fitness functions, widened.**

1. **Import-guard.** The static check that lets only the renderer module import the marker reader now also covers the roster and verdict readers: only the renderer module may read `persona_roster` or `persona_verdicts`. A new importer in any decision module is a boundary violation, caught at review before merge.
2. **NFR-3 fault-injection.** The render-fault test gains a malformed-roster case — a roster that is not a list, or whose names do not match `_active.persona` — and asserts `WorkerResult` and the worker exit code stay byte-identical to a clean run. A malformed roster is a cosmetic glitch, never an execution effect.

**Consequences of the amendment.**

- *Positive.* The persona fan-out has a settled, contention-free shape to write against — the prerequisite the drill-down node needed before any skill writes the new keys.
- *Negative.* A second additive contract on `exec-state.json` must be kept non-gating, and the import-guard must grow to two more keys; deleting it reopens the invariant for them too.
- *Reversal cost.* Once `exec:review` writes the keys and the renderer reads them, retiring the contract is a coordinated change across two repos (the pack and `drain-cycle`), not the single-line revert the original `_active` pointer enjoyed. The forward path stays cheap — an old pack that drops the roster degrades cleanly — but removing the keys after adoption does not.

**Relationship.** The single-string `_active.persona` rule is preserved, not replaced — the roster sits beside it and the renderer combines the two. `persona_roster` extends the `_active` non-section pointer; `persona_verdicts` is additive inside the pack-owned `review` section, so [ADR 0030](0030-execution-state-file.md)'s section ownership is unchanged.

**Plan-review conditions, met.** The REVISE round raised five; each is settled above. (1) The derivation reads each persona's own completion key, not its roster position, so it does not mislabel state under a parallel fan-out. (2) The durability of a dropped verdict is stated honestly — missing until end of run, not one tick — and accepted as a missing-not-wrong mark. (3) The reversal cost across two repos is recorded in Consequences. (4) The write rule is named for what it guarantees (no writer reads another's value), not "race-free," which the file-level clobber would belie. (5) Carrier settled against ADR 0030 (roster under `_active`, verdicts inside the `review` section, no new file or writer on `active.json`), schema-compat confirmed against `drain_cycle/handoff.py` (both keys ignored by the only reader), enforcement named (import-guard widened to the two keys, NFR-3 fault-injection gains the malformed-roster case), and the display-only invariant restated for the new keys with the falsifier that rejects any read-modify-write shape.
