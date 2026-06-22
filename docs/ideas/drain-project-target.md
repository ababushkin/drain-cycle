# Triage record: drain-project-target

## Raw intake
<!-- Verbatim capture of the idea as received. Do not edit. -->
Let me drain a single Linear project on its own, not only the team's active cycle. Today a zero-arg `drain-cycle` always drains the active cycle, so a project's issues and unrelated cycle issues run in the same drain and the run mixes two concerns. I want a `--project <name-or-id>` flag that drains one project's pending issues directly.

## Refined intent
<!-- The confirmed restate from the elicitation loop, in the user's own words. -->
- Outcome: `drain-cycle --project <name|id>` drains every pending issue in one Linear project, ignoring cycle membership.
- User: the operator running drain-cycle, who wants to clear one project's work as its own run.
- Why now: project work and cycle work share a drain today; the run mixes two concerns and the scorecard cannot isolate a project's outcomes.
- Success: the operator runs one project's Todo/Backlog issues end to end, with the run log, scorecard, resume, telemetry, and console all scoped to that project.
- Constraint: reuse the existing run machinery; drain scope is state types `backlog` and `unstarted`; one project at a time.
- Out of scope: wider state filters, project-and-active-cycle intersection, multi-team orchestration, any identity-field schema rename.

## Problem restatement
<!-- "For [customer segment], we believe [problem] is causing [negative outcome]." -->
For the drain-cycle operator, we believe a drain that targets only the active cycle forces project work to ride along in cycle runs, which mixes two concerns in one run and stops the scorecard from grouping a project's outcomes on their own.

## Evidence
<!-- What evidence exists that this problem is real and affects the named customer? -->
The operator's own stated need — drain a chosen project directly. No usage data; a single operator's framing of a workflow they run today.

This routes to build, not a validation slot: it is a scoped capability extension of an existing tool with a settled design, not a speculative proposal whose value is in doubt. The one real unknown was an implementation choice (reuse the identity field or rename it), resolved in [ADR 0033](../adrs/0033-project-drain-identity.md).

## Routing
<!-- idea bank | validation slot | build -->
**Build.** A scoped capability with a settled design — overload `cycle_id` to hold the project id ([ADR 0033](../adrs/0033-project-drain-identity.md)) — not an open unknown. Routed to `shape:delivery` and decomposed bottom-up: N01 (this record + ADR 0033) → N02 (project-scoped Linear queries) → N03 (orchestrator project mode) → N04 (the `--project` CLI flag). N02, N03, and N04 each build on the one before; N01 lands first.

## Notes
<!-- Anything a future reader needs: related items, strategic context. -->
- The one design decision worth recording — reuse `cycle_id` rather than rename it — is in [ADR 0033](../adrs/0033-project-drain-identity.md).
- Full scope, the settled identity decision, and the bottom-up build order live in the Linear project "Drain a chosen Linear project, not only the active cycle".
