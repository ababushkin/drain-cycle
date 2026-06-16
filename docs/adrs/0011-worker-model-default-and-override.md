# ADR 0011: Workers default to Sonnet; a `model:` label overrides per issue

**Date:** 2026-05-23
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §7

A spawned `claude -p` worker inherits whatever model the operator has globally pinned. In the diagnosed quota-burn run all five workers ran on `claude-opus-4-7` (the operator's global pin), and Opus was the single largest cost multiplier of the ~108M-token spend. Workers now default to `claude-sonnet-4-6`, passed explicitly via `--model`; an individual issue opts up (or down) with a `model:<alias>` Linear label, mirroring the `repo:<name>` mechanism. Known aliases (`sonnet`/`opus`/`haiku`) map to full ids; an unrecognised value is passed to `claude --model` verbatim.

**Why lenient, not strict.** Unlike `repo:` resolution — where a missing label is a hard halt because there is no safe default target — model resolution always has a safe fallback. So it never raises: no label, an unknown alias, or conflicting `model:` labels all fall back to the default rather than halting an unattended cycle over a label typo. The model actually used is recorded in the run log, so a mis-labelled issue surfaces after the fact instead of stalling the run.

**Grouped model labels.** A child of the Linear `model` group — e.g. the leaf `sonnet` under `model` — renders as `model:sonnet` via the same `_label_name` projection described in ADR 0009. `model.resolve` sees it identically to a flat `model:sonnet` label.

**Alternatives considered.** (A) Keep inheriting the global pin — rejected, it is exactly what caused the burn and gives the operator no per-issue control. (B) A single global `--model` flag with no per-issue override — simpler, but a cycle legitimately mixes cheap mechanical issues with a few that warrant Opus; per-issue is the right grain. (C) Raise on ambiguous labels like `repo:` does — rejected, halting a whole unattended cycle over a duplicate label is worse than silently taking the cheap, safe default.
