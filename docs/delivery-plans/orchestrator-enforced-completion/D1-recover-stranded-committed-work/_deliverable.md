---
layer: deliverable
id: D1
title: Recover stranded committed work
serves_kr: KR1
maps_to: linear-milestone
external_window: none
---

# D1 — Recover stranded committed work

**Serves:** KR1 — the orchestrator drives committed-but-unfinished issues to completion via a
finishing sub-agent before halting, and never trusts uncommitted work.

A drain worker can exit having done the real work — reviewable commits sit on the issue branch —
yet leave the issue uncompleted: not Done, or Done with no submitted `pr_urls`. Today both states
hit a terminal halt that reverts the issue and strands the branch. This deliverable closes that gap
with one orchestrator-owned recovery seam: detect committed-but-unfinished, spawn a finishing
sub-agent that owns submission, re-check, and fall through to the existing halt only on failure.

## Nodes

- [N01 — Finishing sub-agent recovers committed work](N01-recover-stranded-committed-work.md) ·
  `story` · skeleton
