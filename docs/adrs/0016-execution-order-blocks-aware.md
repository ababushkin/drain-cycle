# ADR 0016: Execution order — manual drag-order only, blocks-aware

**Date:** 2026-05-26
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §12

Before this change the orchestrator ordered issues by `(priority, sortOrder)` and ignored blocks/blocked-by entirely. Priority overrode the operator's manual drag-order, and a blocked issue could run before its blocker — wasting a worker on work that couldn't succeed.

**Decision.** Order purely by manual `sortOrder` ascending; drop `priority` from sorting. Overlay a topological pass (Kahn's algorithm) using `(sortOrder, id)` as the tiebreak among ready issues: each issue is "ready" once all its intra-drain blockers have been scheduled. This preserves the operator's intended drag-order as far as dependencies allow.

**External unresolved blocker → defer.** If a pending issue is blocked by an issue not in this drain's runnable set — including an In Progress issue in the same cycle, which the drain won't complete — the blocked issue is deferred: left Todo, not spawned, logged to stderr. Deferral cascades: if X is deferred and X intra-drain-blocks Y, Y defers too (falls out naturally from Kahn, since Y's in-degree never reaches zero while X is treated as permanently deferred). The exclusion rule is "defer unless the blocker is `completed` or `canceled`" — so `started`/`unstarted`/`backlog`/`triage` external blockers all defer. Deferred issues are not run-logged: they were never attempted, and counting them in `done/attempted` would corrupt `grade`'s completion signal.

**Intra-drain cycle → halt.** If the blocks graph among the pending issues contains a cycle (self-loop included), `DependencyCycleError` is raised, the orchestrator sets `cycle_halt_reason`, prints a `Halt:` line naming the involved issues, and exits 1. Nothing runs.

**`pending_issues` return type changed from `list[dict]` to `ExecutionPlan`.** The pure function `_plan(issues) -> ExecutionPlan` replaces `_sort_pending_issues`. `ExecutionPlan` is a frozen dataclass carrying `order: list[dict]` (runnable issues, topo-sorted) and `deferred: list[dict]` (each entry has `issue`, `blocker_identifier`, `blocker_state_type`). `DependencyCycleError(RuntimeError)` carries `identifiers: list[str]` for the cycle report. The `inverseRelations` GraphQL field is fetched in `pending_issues`, flattened to `issue["blockers"] = [{id, identifier, state_type}]`, and the raw key dropped — wire shape stays local to `pending_issues`, like `labels`.

**All-deferred → exit 0.** If `plan.order` is empty but `plan.deferred` is not, the run emits the stderr deferral lines and returns 0: the cycle isn't broken, it's just blocked externally. An empty `plan.order` with an empty `plan.deferred` is also exit 0 ("nothing to do").

**Alternatives considered.**

- *Keep priority in the sort key.* Rejected: the operator has one predictable knob — the manual drag-order — and priority overriding it is surprising and undesirable.
- *Ignore blocks entirely.* Rejected: running a blocked issue wastes a worker on work that can't succeed by definition.
- *Best-effort run instead of defer/halt.* Rejected: silently violating a dependency the operator encoded is worse than a loud skip or stop.
- *Record deferrals in the run log.* Rejected: a deferred issue was never attempted; counting it as an entry corrupts `done/attempted` in `grade`.
