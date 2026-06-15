---
layer: node
id: N03
type: story
title: ANSI colour layer
parent: D2
serves_kr: KR1
maps_to: linear-issue
external_window: none
completion:
  form: acceptance-criteria
  criterion: >
    Transcript lines carry tty-gated ANSI styling — dim results and status, a single accent
    colour for tool names, red for error results, dim-italic thinking — and non-tty output
    stays byte-plain.
delegates_to: at-pickup task breakdown (story)
---

# N03 — ANSI colour layer

**Type:** `story`.

> **Blocked by:** [N02](N02-transcript-rendering-core.md) — Graphite stack PR 3 styles the
> rendering core's output.

## What

As the drain-cycle operator, I want the pane transcript styled the way interactive Claude Code
styles it — tool results and status dim, tool names in one accent colour (cyan), error results
red, thinking dim-italic — so that my eye separates Claude's prose from tool mechanics without
reading every line. Styling is hand-rolled ANSI escape constants gated on `out.isatty()`, exposed
as a `color` keyword on the formatter (default: auto-detect) so the StringIO-based test suite
stays plain-text.

## Why

The bet: four ANSI constants (dim, italic, cyan, red + reset) cover everything this surface
needs. Rejected alternative: `rich.Console` — already a project dependency, but its markup parser
treats `[...]` in arbitrary assistant/tool text as style tags, so every write needs
`rich.markup.escape`; one missed call corrupts rendering or kills the filter, and the pane
degrades to raw JSON via `|| cat`. For a never-crash pipe filter, no-dependency string constants
are strictly safer. (`drain_cycle/console.py`'s rich usage is orchestrator stderr — a different
surface, no consistency obligation.) Landing colour after the plain core keeps PR 2 reviewable as
pure text and makes this PR a small, revertable styling diff.

## Completion

- **Done when:** with `color=True`, status/result lines carry the dim escape, tool names the
  accent escape, `is_error` results red, thinking lines dim-italic, each terminated by reset.
- **Done when:** with the default on a non-tty stream, output contains no `\x1b` byte (existing
  plain-text assertions pass unchanged).
- **Done when:** the filter's crash-resilience battery from N01 still passes with colour enabled.

## Assumptions

- The formatter's stdout in the pane is the pane tty (it is the last stage of the pipe), so `isatty()` auto-detection turns colour on in real use without a flag. *(verified — pipeline structure in `_open_watch_pane`)*

## Key Risks

- **Risk:** styling logic interleaved into every render call makes the core unreadable and easy
  to break.
  *Mitigation:* style application is confined to small helpers (style-wrap functions returning
  the bare string when colour is off), so render logic stays string-shaped and the colour path is
  a thin layer over N02's output.

## Tasks

- [ ] `skeleton` — `color` keyword with tty auto-detect and the ANSI constant set, one styled line end-to-end (dim status line) proving the gate works (helper functions folded in) · Done when: `color=True` emits the dim escape on the status line and a StringIO run emits no `\x1b` · Model: Fast · risk reversible · review standard · axes RC·SC·HS·SR·OR = L·L·L·L·L
- [ ] Apply styles across the transcript — accent tool names, dim previews, red `is_error`, dim-italic thinking · Done when: each styled line form is asserted with `color=True` and all plain-text tests pass unchanged · Model: Fast · risk reversible · review standard · axes RC·SC·HS·SR·OR = L·L·L·L·L
