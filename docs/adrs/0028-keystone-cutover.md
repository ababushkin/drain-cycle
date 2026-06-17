# ADR 0028: The keystone cutover — `prompt.py` collapses to a pointer at `exec:pickup`

**Date:** 2026-06-16
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §24

The architecture ([`architecture.html`](../architecture.html) §3) describes Layer 1 as carrying no procedure: the worker's prompt points at the entry skill and the workflow lives in Layer 2. **Reaching that state in code is one concrete change — strip `prompt.py` from inlining the workflow down to a thin pointer at `exec:pickup`.** This is the keystone: the cutover that turns the two-layer split from a description into running code. Tracked by the "drain-cycle supervises; the pack owns the workflow" project.

This entry, not the architecture doc, is where the cutover's status lives — the architecture describes the structure as designed, "as is"; a migration that hasn't fully landed is decision-log material, not architecture.

**Current state and what the cutover changes.** Today `prompt.py` inlines the worker procedure as prose and names a few skills directly — the current `/code-review-and-quality` (review) and `/shape:pr-finishing` (finish). At the cutover those references swap to the `exec:*` namespace (`exec:review`, `exec:finish`; pack ADR 0004 / Shaper `execution-workflow` design doc), and the inlined procedure collapses to the single pointer. After it lands the supervisor names no workflow steps at all — only the entry skill — so any vendor's worker follows the same Layer-2 prose.

**Why it's the keystone.** The properties the architecture describes — the content-blind artifact boundary, dual-mode, vendor-agnostic workers — all rest on the supervisor not carrying procedure. This is why ADR 0020 / ADR 0022's orchestrator-assembles-the-stack design was reversed in ADR 0023: hard-coding the `gt`/`gh` sequence into Layer 1 keeps procedure in the supervisor and blocks the cutover.
