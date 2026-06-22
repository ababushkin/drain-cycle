# ADR 0033: A project drain overloads `cycle_id`; it does not rename the field

**Date:** 2026-06-22
**Status:** Accepted

drain-cycle drains the team's active cycle. A new `--project` flag lets the operator drain one Linear project instead. Both runs reuse the same machinery — run logs, the scorecard, the resume-glob, telemetry, the console header — and all of it keys on a field named `cycle_id`. This ADR settles what that field holds when the target is a project.

**Decision.** A project drain **overloads** `cycle_id`: the field carries the project id. We do not rename it to a neutral identity field. `cycle_id` is opaque run identity — it holds a cycle id for a cycle drain and a project id for a project drain.

## Context

`cycle_id` is read at 13 sites. Two of them carry real cycle semantics:

- `current_cycle_id()` (`drain_cycle/linear.py`) asks Linear for the team's active cycle.
- the `cycle: { id: { eq: $cycleId } }` filter in `pending_issues()` (`drain_cycle/linear.py`) fetches the issues that belong to that cycle.

The other 11 reads treat the value as opaque identity — a string they key, group, glob, label, or thread through without reading what it names: run-log file naming and the `cycle_id` record field (`drain_cycle/runlog.py`), scorecard grouping (`drain_cycle/scorecard.py`), the resume-glob (`drain_cycle/orchestrator.py`), the OpenTelemetry `drain.cycle_id` attribute (`drain_cycle/orchestrator.py`), the console header (`drain_cycle/console.py`), and the `cycle_id` parameter the orchestrator threads between them.

A rename would touch 29 production sites and 136 test sites, and break every existing run-log archive. Archived run logs already on disk carry a `cycle_id` key, and the scorecard groups by it; a schema rename would either orphan those archives or force a migration of files already written.

Project mode replaces only the two cycle-semantic reads — it resolves a project id and filters by project instead of cycle — and leaves the 11 opaque sites untouched. The scorecard then groups a project's runs as their own unit at no extra cost, because grouping already keys on whatever `cycle_id` holds.

## Consequences

**Positive.**

- Project mode reuses run logs, the scorecard, resume, and telemetry unchanged. The new code is two reads — project resolution and a project filter — not a field rename across 165 sites.
- Existing run-log archives stay readable, and the scorecard keeps grouping them.

**Cosmetic.**

- The field name now lies for a project drain: `cycle_id` holds a project id. Anyone reading the code or a raw run log sees `cycle_id` and has to know it is overloaded. This ADR is that record.
- The operator-facing and telemetry labels would read "cycle" for a project run. A `target_kind` label — values `cycle` or `project` — fixes the wording at the display sites (the console header, the telemetry attribute) so the operator sees the right word. The label is cosmetic: it changes no decision path and no stored identity. The orchestrator project-mode work that follows this ADR introduces it.

## Alternatives considered

- **Rename `cycle_id` to a neutral pair** such as `{source_type, source_id}`. Rejected. The honest name is not worth 29 production and 136 test edits plus a run-log archive migration, for a field that is opaque at 11 of its 13 reads. The `target_kind` label buys the operator-facing honesty at the display layer for a fraction of the cost.
- **Add a separate `project_id` field** alongside `cycle_id`. Rejected. Every opaque consumer — run-log keying, scorecard grouping, the resume-glob — would then branch on which field is set, doubling the identity surface the run record carries for no semantic gain. One opaque identity field plus a `target_kind` label is simpler.
