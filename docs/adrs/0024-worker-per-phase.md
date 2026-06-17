# ADR 0024: A worker per phase, not a worker per issue

**Date:** 2026-06-15
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §20

Today one spawned worker runs the whole `exec:*` chain for an issue — pickup through finish — in a single session. The decision is to split it: each phase (code, review, finish) is its own spawn, with its own model tier, and the agent that produced an artifact never reviews it. See [`architecture.html`](../architecture.html) §4.

**Why split.** Two independent reasons, either sufficient on its own:

- *Independent verification.* A coder reviewing its own work rationalises its own choices — the review inherits the blind spots of the build. A separate review agent that did not write the code is adversarial by construction, not by prompt wording. This is the same logic that made `exec:review` fan out to distinct personas (the persona contract); phase separation extends it across the build/review boundary, not just within review.
- *Per-phase model economics.* The operator anchors review to a stronger, more expensive model regardless of what built the change — a cheap model codes, an expensive one reviews. That asymmetry is only expressible if each phase is its own `claude -p` spawn with its own `--model` pin. A single worker pins one model for the whole chain.

**Cost accepted.** Every phase boundary now pays a spawn plus artifact rehydration: the next phase starts cold and reads its inputs from the execution-state file ([architecture.html](../architecture.html) §5) rather than inheriting them in context. A single worker kept that context for free. This makes the execution-state file load-bearing for *all* cross-phase state, not just `pr_urls` — the file must carry what each phase needs the next to know. This is the deliberate price of independence and the asymmetric-model economics.

**Alternatives considered.**

- *One worker runs the whole chain (status quo).* Rejected: it forecloses both independent review and per-phase model pinning — the coder grades itself, on the model that built the change.
- *One worker, but review re-spawned as a sub-agent within it.* Rejected as a half-measure: it buys independent review but not independent model economics at the phase grain, and it keeps the chain's lifecycle coupled to one outer session that the control plane cannot steer phase-by-phase ([architecture.html](../architecture.html) §10).
