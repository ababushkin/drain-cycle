# ADR 0021: `/shape:task` runs inside the worker session; whole-project shaping stays at design-doc scope

**Date:** 2026-06-01
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §17

> **Forward-pointer.** Not stale today, but the target moved into the pack. Under the `exec:*` namespace (pack ADR 0004 / Shaper `execution-workflow` design doc), `/shape:task` folds into `exec:pickup`/`exec:breakdown` at the keystone cutover (ADR 0028). The in/out contract below still describes what that step does.

The M2 worker pipeline introduces a Task Shaper role — `/shape:task` — that runs before the implementer on verify-flow tickets. The initiative doc deferred the invocation interface to this ADR: whether whole-project shaping (via `/shape:planning-and-task-breakdown`) pre-computes per-ticket task lists that workers later read, or `/shape:task` runs inside each worker session on its own ticket.

**Decision: `/shape:task` runs inside the worker session, invoked once per ticket before the implementer begins.** The skill takes a Linear issue identifier, fetches the live issue from Linear via MCP, and produces an enriched AC checklist + sizing decision + per-stack task list. The output is fed to the implementer in-session and written to the Linear issue body. The Linear write is the durable artefact — a worker resuming a halted issue (ADR 0018) reads the existing output from the issue body rather than re-invoking the skill.

**Why the per-ticket, in-worker path.** The alternative (pre-computation) would require the operator to run `/shape:planning-and-task-breakdown` before any drain can begin, turning the whole-project skill into a planning gate every drain must pass through. `drain-cycle`'s autonomous-drain promise is a single `drain-cycle` invocation, unattended. Fracturing that into "run planning, then drain" creates ceremony the tool exists to remove. A worker that calls `/shape:task` itself is self-contained: the same invocation path works for a cycle drain, a single-ticket re-run, or a resume (ADR 0018) — no prior planning artefact required.

**Rejected alternative: `/shape:planning-and-task-breakdown` pre-computes per-ticket task lists.** The whole-project skill runs once before the drain, writes structured per-ticket task lists somewhere (a design doc, a Linear comment, an issue body block), and workers read those artefacts at session start. The appeal is operator review of decomposition before any code runs. The costs are: (a) a mandatory pre-drain gate that breaks the single-invocation promise; (b) a machine-readable output contract that `/shape:planning-and-task-breakdown` must emit and every worker must parse — a shared format across two skills and their update paths; (c) stale-data risk when the issue body changes between planning and execution; (d) the only way to know whether the pre-computed list still applies is to re-derive it, making pre-computation an expensive cache that must be manually invalidated. The two skills stay at distinct scopes: `/shape:planning-and-task-breakdown` operates on a whole design doc; `/shape:task` operates on a single Linear issue.

**`/shape:task`'s input/output contract, as constrained by this decision.**

*Input:* A Linear issue identifier. The skill fetches the current issue body, title, and AC from Linear at invocation time. It does not read any prior planning artefact.

*Output:*
- **Enriched AC checklist** — AC items made concrete, implicit contracts surfaced, irresolvable gaps flagged (the skill notes gaps and continues; it does not hang waiting for operator input).
- **Sizing decision** — `single-stack` (all work on one branch) or `N-stacks` (N specified and justified), based on whether the implementation can reach Done in a single PR stack.
- **Per-stack task list** — one ordered task list per stack, intended as direct context for the implementer.

*Dual-write:* Output is (a) returned to the calling worker for in-session context and (b) appended to the Linear issue body. The Linear write is authoritative: a resumed worker reads the block from the issue body instead of re-running the skill. The skill must emit the output in a stable, delimited format that a future invocation can detect (to avoid silently double-writing).
