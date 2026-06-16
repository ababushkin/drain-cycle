# ADR 0013: Resource guardrails — a native cost belt and orchestrator token/time suspenders

**Date:** 2026-05-23
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §9

Before this, the only ceiling on a worker was a 3600s wall-clock timeout, and the cycle as a whole had none. A single diagnosed run burned ~108M tokens across five issues with no circuit-breaker. `drain_cycle/limits.py` now defines per-issue and cycle-wide caps on tokens, wall-clock, and cost, enforced in two layers:

- **Native belt.** The per-issue cost cap is passed to `claude` as `--max-budget-usd`, so the session self-terminates on spend without the orchestrator watching it.
- **Orchestrator suspenders.** The per-issue token and time caps are enforced by the worker against the live event stream: a poll loop compares the running cumulative-token tally and elapsed wall-clock against the caps and SIGKILLs the session's process group on the first breach (reusing the group-kill machinery from ADR 0012). The cycle-wide caps are enforced by the orchestrator between issues — after each Done issue it sums the run log's running totals and stops the run if any cycle cap is crossed.

**Why both layers.** The cost belt is the cheapest possible enforcement — `claude` already meters its own spend — but it only knows about *this* session's dollars, and a subscription user cares about tokens, not dollars. The token cap is therefore the primary guardrail and has to be the orchestrator's job, since `claude` exposes no `--max-tokens` equivalent for a whole session. Time is enforced the same way because a session can wedge while emitting no usage at all (the old 3600s timeout's job), so wall-clock can't be inferred from the token stream.

**Why the cycle caps live in the orchestrator, not the worker.** A worker only sees its own issue. The failure mode the cycle caps exist for is death-by-aggregate: every issue stays comfortably under its per-issue cap while their sum drains the quota (8M × 5 = 40M, past a 30M cycle cap, with no single issue ever breaching). Only the orchestrator, which holds the run log's running totals, can see that — so it checks after each Done issue and stops before spawning the next.

**Defaults: per-issue 8M tokens · 20 min · $15; cycle 30M tokens · 90 min · $60.** These are deliberately generous starting points sized below the diagnosed bad run (one issue alone hit 43M tokens / 23 min), not tuned values. The intent is a circuit-breaker that trips on the pathological case, not a tight budget. They are meant to be recalibrated against real run-log spend.

**Each guardrail is independently on/off-able.** Any cap can be `None` (off). The defaults are all live; an operator turns one off with `null` in the optional `~/.drain-cycle/limits.yml`. With both the per-issue token and time caps off, the worker simply waits for the session to exit on its own — there is no longer any implicit outer timeout, which is the operator's explicit choice when they disable both.

**The time cap absorbed the old 3600s timeout.** Rather than keep a separate hardcoded outer timeout alongside the configurable time cap, the time cap *is* the timeout — `per_issue_seconds` (default 20 min, tighter than the old 3600s and below the diagnosed 23-min overrun). One time concept, configurable, instead of two.

**Breach reporting.** A breach is a small `Breach(scope, metric, limit, observed)` value whose `describe()` renders the operator-facing line — used verbatim by the worker (per-issue kill) and the orchestrator (cycle stop) so the wording can't drift. A per-issue breach takes the existing exit-1 + revert + `halt_reason` contract, naming the cap and the value at kill time. A cycle breach lands in the run log's top-level `cycle_halt_reason`: the breaching issue's own entry is a normal Done, and the top-level field explains why the run stopped.

**`limits.yml` semantics and validation.** Absent key → baked-in default; explicit `null` → guardrail off; positive number → override. A present-but-malformed file (unknown key, non-positive, non-numeric, bool, invalid YAML) raises `LimitsConfigError` and halts at CLI startup — mirroring the eager `repos.yml` validation (ADR 0009). The reasoning is sharper here: silently falling back to defaults on a typo would leave the operator believing a tighter cap was active when it wasn't, which is worse than a loud halt.

**Alternatives considered.**

- *CLI flags to override limits per invocation.* Deferred. The acceptance criteria require only defaults + `limits.yml`, and `cli.main` is a deliberately minimal exact-match dispatcher (decision in `cli.py`); adding an argument parser to thread per-run overrides is scope the single operator can cover by editing `limits.yml`. Revisit if a use case for one-off overrides appears.
- *Kill on the cycle cap mid-stream (pass cycle-so-far totals into the worker).* Rejected as unnecessary: the per-issue cap (8M) is below the cycle cap (30M), so a single issue can't cross the cycle cap before crossing its own; checking between issues catches the aggregate case without coupling the worker to cycle state.
- *A separate hardcoded outer timeout kept alongside the configurable caps.* Rejected — two time concepts where one suffices (see above).
