---
layer: deliverable
id: D3
title: Live usage status in the pane
parent: ..
serves_kr: KR3
maps_to: linear-milestone
completion:
  form: kr-observed
  criterion: >
    KR3 holds — the pane shows live per-turn token/context status and an end-of-run cost
    footer whose numbers match worker._UsageAccumulator on the same fixture stream.
---

# D3 — Live usage status in the pane

**Serves:** KR3 *(foundation)* — "The pane shows live per-turn token/context status and an
end-of-run cost footer whose numbers match `worker._UsageAccumulator` on the same fixture
stream."

The watch pane currently shows activity but no trajectory: the operator can't tell whether a
worker is at 30k or 150k context, or what the run has consumed, without waiting for the final
result event. This deliverable puts a per-turn status line and a final footer in the pane,
sourced from the same `message.usage` data the orchestrator's accumulator reads off the FIFO
branch — same numbers, display side only.

## Nodes

- [N04 — Usage status + footer](N04-usage-status-and-footer.md) · `story`

## Done when

KR3 is observed: on a shared fixture stream, the pane tally's cumulative and peak-context numbers
equal `worker._UsageAccumulator`'s, and the footer renders with and without cost. Reducible to
N04's acceptance criteria — no separate acceptance node.
