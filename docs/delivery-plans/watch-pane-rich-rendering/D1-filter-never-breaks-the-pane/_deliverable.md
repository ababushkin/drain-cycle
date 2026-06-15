---
layer: deliverable
id: D1
title: The formatter filter never breaks the pane or the parse
parent: ..
serves_kr: KR2
maps_to: linear-milestone
completion:
  form: kr-observed
  criterion: >
    KR2 holds — the filter exits 0 and degrades to raw-line echo on any malformed or
    pathological event, the FIFO branch and pipeline string are untouched, and
    tests/test_orchestrator_watch.py passes unchanged.
---

# D1 — The formatter filter never breaks the pane or the parse

**Serves:** KR2 *(brake)* — "Zero observability regression: the filter exits 0 and degrades to
raw-line echo on any malformed or pathological event, the FIFO branch and pipeline string are
untouched, and `tests/test_orchestrator_watch.py` passes unchanged."

This deliverable is the brake, and it is the base of the Graphite stack on purpose: every
rendering change in D2/D3 sits on top of a filter that is already proven crash-proof. The
`|| cat` brace group in `orchestrator._watch_formatter_stage()` exists so a dead formatter can't
SIGPIPE `tee` and truncate the FIFO; the filter's side of that contract is exit-0 plus raw-line
echo on anything unrenderable. Today only `json.JSONDecodeError` is caught — a bug anywhere in
rendering kills the filter and degrades the pane to raw JSON.

## Nodes

- [N01 — Crash-proof filter](N01-crash-proof-filter.md) · `story`

## Done when

KR2 is observed: the crash-resilience test battery passes, `tests/test_orchestrator_watch.py`
passes with no changes to it or to `orchestrator.py`, and the filter's exit code is 0 on every
fixture. Reducible to N01's acceptance criteria — no separate acceptance node.
