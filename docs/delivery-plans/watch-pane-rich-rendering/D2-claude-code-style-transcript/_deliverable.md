---
layer: deliverable
id: D2
title: Claude-Code-style transcript in the pane
parent: ..
serves_kr: KR1
maps_to: linear-milestone
completion:
  form: kr-observed
  criterion: >
    KR1 holds — the pane renders assistant prose and tool calls as ⏺ bullets with per-tool
    meaningful arguments, ⎿ result previews, and ✻ thinking markers, verified by unit tests
    plus a tmux capture-pane replay of a captured real session.
---

# D2 — Claude-Code-style transcript in the pane

**Serves:** KR1 *(bet)* — "The pane renders assistant prose and tool calls as ⏺ bullets with
per-tool meaningful arguments, ⎿ result previews, and ✻ thinking markers — verified by unit
tests plus a tmux `capture-pane` replay of a captured real session."

This deliverable is the bet: the operator reads the pane the way they read an interactive Claude
Code session. Three nodes, three stacked PRs: the glyph/argument rendering core (N02), the
tty-gated ANSI colour layer on top of it (N03), and the operator-facing docs plus the end-to-end
pane replay that closes the KR (N05). The colour layer is deliberately a separate PR from the
rendering core: plain-text rendering is reviewable and shippable on its own, and colour is the
part most likely to need taste-driven iteration.

## Nodes

- [N02 — Transcript rendering core](N02-transcript-rendering-core.md) · `story`
- [N03 — ANSI colour layer](N03-ansi-color-layer.md) · `story`
- [N05 — Docs + pane replay](N05-docs-pane-rendering.md) · `story` · carries the
  `acceptance`-tagged replay task that closes this deliverable

## Done when

KR1 is observed: the rendering unit tests pass and N05's replay task shows a captured real
session rendering as ⏺/⎿/✻ transcript lines in an actual tmux pane while the FIFO copy stays
byte-identical stream-json. The replay is cross-seam (rendering core + colour + real pipeline),
so it lives as the `acceptance` task on the last node rather than being re-runnable from any
single node's criteria.
