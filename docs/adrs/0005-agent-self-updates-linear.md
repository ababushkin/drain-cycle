# ADR 0005: The spawned agent updates Linear itself

**Date:** 2026-05-22
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §1

> **Superseded-by (pending).** The "Multi-agent collaboration for correctness" Layer-1 project replaces self-asserted Done with a **verifier-gated Done** contract: a ticket reaches Done only with a recorded `outcome_verdict` (its KR2). The supervisor records the verdict; the agent no longer asserts success unobserved. Until that lands, the decision below stands.

The orchestrator does **not** poll Linear and write status. The spawned `claude -p` session is told, in its prompt, to move its issue to Done on completion. The orchestrator only reads Linear after the session exits, to decide whether to advance or halt.

**Alternative considered.** Orchestrator-owned status: the parent polls Linear, transitions states, owns the lifecycle. This is more conventional and easier to reason about.

**Why the agent-self-update path.** The orchestrator can only observe *process exit*, not *task success*. A Claude session may exit 0 having done nothing useful, or exit non-zero having actually shipped — exit code is a poor proxy for "the issue is Done." Letting the agent assert Done in Linear forces it to make an explicit, observable claim about its own outcome, which is exactly the artefact we need to grade KR1 and trigger the kill condition. If this pattern proves unreliable, that's the initiative's kill condition firing — not a bug to paper over.
