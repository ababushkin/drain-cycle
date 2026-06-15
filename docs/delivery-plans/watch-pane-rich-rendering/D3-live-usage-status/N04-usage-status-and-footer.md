---
layer: node
id: N04
type: story
title: Usage status + footer
parent: D3
serves_kr: KR3
maps_to: linear-issue
external_window: none
completion:
  form: acceptance-criteria
  criterion: >
    Each turn boundary shows a dim token/context status line, the run ends with a
    turns/cost/usage footer, and the tally's numbers match worker._UsageAccumulator on the
    same fixture.
delegates_to: at-pickup task breakdown (story)
---

# N04 — Usage status + footer

**Type:** `story`.

> **Blocked by:** [N03](../D2-claude-code-style-transcript/N03-ansi-color-layer.md) — Graphite
> stack PR 4; the status line uses the dim styling helper.

## What

As the drain-cycle operator, I want each turn boundary to show
`· Turn N · <tok> tok · <ctx> ctx` and the run to end with
`· done · N turns · $X.XX · <cumulative> tok · peak <peak> ctx`, so that I can see a worker's
context growth and spend trajectory live instead of only in the run log afterwards. The numbers
come from a small usage tally inside the formatter that mirrors
`worker._UsageAccumulator` (`drain_cycle/worker.py`): per-`message.id` last-copy-wins over the
token fields in `message.usage`, context = input + cache_read + cache_creation, cumulative and
peak derived the same way. Token counts format via the existing `fmt_tokens`
(`drain_cycle/progress.py`) — e.g. `92k`.

## Why

The bet: mirroring the accumulator's per-id last-copy-wins semantics in a tiny display-side tally
is enough — the pane's numbers and the run log's numbers come from the same arithmetic over the
same events, so they can be asserted equal in tests. Rejected alternative: importing and reusing
`worker._UsageAccumulator` directly — it is thread-safe and lifecycle-coupled to the worker's
breach monitor, none of which a single-threaded stdin filter needs, and importing `worker` into
the filter drags subprocess machinery into a module whose job is string formatting. Rejected
alternative: showing live cost per turn — stream-json carries no cost until the final `result`
event (`assistant` events have `message.usage` only), so any live dollar figure would be a local
re-derivation of Anthropic pricing that silently drifts; tokens/context live, cost at the end.

## Completion

- **Done when:** each new `message.id` boundary renders one dim status line with turn number,
  cumulative tokens, and context size formatted via `fmt_tokens`.
- **Done when:** the `result` event renders the footer with cost, and a cost-less `result`
  (killed/partial run) renders the footer without the `$` segment.
- **Done when:** on a shared fixture stream (including duplicate events per `message.id` and a
  mid-stream usage correction), the tally's cumulative and peak-context equal
  `worker._UsageAccumulator`'s values.

## Assumptions

- Live cost is absent from stream-json before the final `result` event. *(verified — `assistant` events carry `message.usage` only; `total_cost_usd` arrives once, at the end)*
- Last-copy-wins per `message.id` is the correct accumulation rule for repeated per-block events. *(verified — it is exactly what `worker._UsageAccumulator` does on the FIFO branch)*

## Key Risks

- **Risk:** the display tally and the orchestrator accumulator drift apart over time as one is
  updated without the other, and the pane quietly shows wrong numbers.
  *Mitigation:* the equality test pins them to the same fixture — a semantic change to either
  side breaks the suite until both move together.

## Tasks

- [ ] `skeleton` — Usage tally (per-id last-copy-wins) feeding a dim per-turn status line end-to-end (fmt_tokens reuse folded in) · Done when: the fixture stream renders one status line per message id with cumulative and context values · Model: Balanced · risk reversible · review standard · axes RC·SC·HS·SR·OR = M·M·L·L·L
- [ ] Final footer with turns/cost/cumulative/peak and the cost-absent variant, plus the accumulator-equality test · Done when: both footer forms render and the tally equals `worker._UsageAccumulator` on the shared fixture · Model: Fast · risk reversible · review standard · axes RC·SC·HS·SR·OR = L·M·L·L·L
