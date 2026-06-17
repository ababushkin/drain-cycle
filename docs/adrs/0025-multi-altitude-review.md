# ADR 0025: Review is multi-altitude; higher-altitude review yields new work, not reverts

**Date:** 2026-06-15
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §21

Review fires at three altitudes matching the delivery hierarchy `shape:delivery` already produces — task, milestone, project — not only per task. The full model is in [`architecture.html`](../architecture.html) §12.

**Why multi-altitude.** A task review is diff-bounded: it grades one issue's change against its AC and the quality lenses. It cannot see whether the tasks of a milestone *cohere*, whether landing them degraded something *outside* their own boundary, or whether the project met its stated goals. Those are real defect classes that only exist at a higher altitude, so they need a review oriented to that altitude. Verification rolls up the same tree the work was decomposed down — the dual of the delivery hierarchy.

**The load-bearing consequence: higher-altitude review produces new remediation work, not reverts.** Task review can halt before a PR merges, so its verdict can block a not-yet-merged artifact. A milestone or project review runs *after* its child PRs have merged; a merged slice cannot be cleanly reverted. So a failing milestone/project review emits new Linear issues slotted back into the plan — it does not roll back landed work. This is a genuinely different halt semantic from the task-level halt/revert contract (ADR 0007, ADR 0013) and must be modelled as such: the supervisor's tree walker reacts to a failing altitude verdict by *scheduling*, not *reverting*.

**Two unknowns deferred to spikes, not decided here.**

- *Regression-review blast radius.* "Did landing this milestone degrade anything outside its boundary" is not diff-bounded the way task review is — it needs a definition of the boundary and probably a cross-cutting test/check run. Shape it with `shape:design` before building.
- *Remediation routing.* When an altitude review fails, what exactly is created (a new issue under the same milestone? a blocking flag on the project?), and does the supervisor pause the parent or keep draining siblings? This is the verdict-handoff open seam (architecture "Known open seam") widened to higher altitudes.

**Alternatives considered.**

- *Task review only; trust that coherent tasks compose into a coherent milestone.* Rejected: integration and regression defects are exactly the ones that survive a green per-task review, because no task-level lens is oriented to find them.
- *Run all altitude reviews inline at project end.* Rejected: a milestone defect found only at project end is far more expensive to remediate than one caught when the milestone closed, and project-end is too late to inform the next milestone's work.
