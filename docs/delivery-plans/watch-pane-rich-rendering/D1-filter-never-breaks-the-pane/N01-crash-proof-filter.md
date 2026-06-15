---
layer: node
id: N01
type: story
title: Crash-proof filter
parent: D1
serves_kr: KR2
maps_to: linear-issue
skeleton: true
external_window: none
completion:
  form: acceptance-criteria
  criterion: >
    The watch_format filter survives any single bad event by echoing the raw line, exits 0 on
    pathological input, and handles tool_result content in both its string and list forms.
delegates_to: at-pickup task breakdown (story)
---

# N01 — Crash-proof filter

**Type:** `story`. Base of the Graphite stack — no upstream node.

## What

As the drain-cycle operator, I want the watch-pane filter to survive any malformed or unexpected
stream-json event by echoing the raw line instead of dying, so that a single rendering bug can
never degrade the whole pane to raw JSON for the rest of the session. Scope: harden
`run()` in `drain_cycle/watch_format.py` (today only `json.JSONDecodeError` is caught around
parsing; nothing guards `formatter.feed`), and make tool_result handling accept `content` as
**string or list** — stream-json emits both forms; the current code assumes list.

## Why

The bet: hardening lands first so every rendering change stacked on top (N02–N04) is written
against a filter that already cannot crash — the riskier the rendering gets, the more the catch-all
earns. Rejected alternative: hardening bundled into the rendering rewrite — that couples the brake
to the bet, so a revert of the rendering PR also reverts the safety net. Landing it as the stack
base unlocks every later PR being individually revertable without touching resilience. The pane's
`|| cat` fallback in `orchestrator._watch_formatter_stage()` is the outer half of this contract
(it protects `tee` and the FIFO from a dead formatter); this node is the inner half (the formatter
doesn't die in the first place).

## Completion

- **Done when:** an exception raised anywhere inside per-event rendering results in the raw
  stripped line on stdout, the loop continuing, and `run()` returning 0.
- **Done when:** tool_result events whose `content` is a plain string render without error,
  matching the list-form behaviour.
- **Done when:** a pathological-shape fixture battery (non-dict `message`, `content` list of bare
  strings, string-valued `usage` fields, non-dict tool `input`) passes with exit code 0 and no
  traceback.
- **Done when:** `tests/test_orchestrator_watch.py` passes with zero changes to it or to
  `drain_cycle/orchestrator.py`.

## Assumptions

- `tool_result.content` arrives as a string in some real sessions and as a list of blocks in others. *(verified — observed in captured stream-json output)*
- The pane pipeline string in `orchestrator._watch_formatter_stage()` needs no change for any of this work; `tests/test_orchestrator_watch.py` pins it. *(verified)*

## Key Risks

- **Risk:** the catch-all swallows a systematic bug silently — every event echoes raw and nobody
  notices the formatter is effectively dead.
  *Mitigation:* the catch-all echoes the raw line (visible as JSON in the pane — loud, not
  silent), and the crash-resilience tests assert specific render output for well-formed events, so
  a formatter that only echoes fails the suite.

## Tasks

- [ ] `skeleton` — Wrap per-event rendering in a catch-all that echoes the raw line and keeps the loop alive (regression test driving a raising render path folded in) · Done when: a monkeypatched raising feed yields the raw line on stdout and `run()` returns 0 · Model: Balanced · risk reversible · review standard · axes RC·SC·HS·SR·OR = M·L·L·L·L
- [ ] Accept tool_result `content` as string or list, including empty content · Done when: string-form, list-form, and empty-content fixtures each render the size line without error · Model: Fast · risk reversible · review standard · axes RC·SC·HS·SR·OR = L·L·L·L·L
- [ ] Add the pathological-shape fixture battery to `tests/test_watch_format.py` · Done when: non-dict message, bare-string content items, string usage values, and non-dict input all pass with rc 0 and no traceback · Model: Fast · risk reversible · review standard · axes RC·SC·HS·SR·OR = L·L·L·L·L
