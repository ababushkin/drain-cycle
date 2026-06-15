---
layer: node
id: N05
type: story
title: Docs + pane replay
parent: D2
serves_kr: KR1
maps_to: linear-issue
external_window: none
completion:
  form: acceptance-criteria
  criterion: >
    Operator docs show the new pane rendering, the stale design-decisions sentence is
    corrected, and a captured real session replayed through the actual tmux pane pipeline
    renders as transcript lines with a byte-identical FIFO copy.
delegates_to: at-pickup task breakdown (story)
---

# N05 — Docs + pane replay

**Type:** `story`. Top of the Graphite stack; carries D2's `acceptance`-tagged replay task and
closes last.

> **Blocked by:** [N04](../D3-live-usage-status/N04-usage-status-and-footer.md) — Graphite stack
> PR 5; the replay and the README sample must show the finished rendering, status lines included.

## What

As the drain-cycle operator, I want the README's "Inspecting a live run" section to show what the
watch pane actually looks like now, so that the docs match the product and the old raw-JSON
expectation dies. Scope: a fenced sample of the new pane output in `README.md` (noting cost
appears only at the end), and correcting the sentence in `docs/design-decisions.md` that still
claims "the pane shows precisely what `claude` emits" — stale since the formatter landed; the
FIFO-independence rationale around it is unchanged and stays. This node also carries the
end-to-end replay that closes KR1: a captured real stream-json session played through the actual
pane pipeline (`tee <fifo> | { formatter || cat; }`) in a throwaway tmux session, captured via
`capture-pane`.

## Why

The bet: a doc sample plus a real-pipeline replay is the cheapest honest close for a visual
feature — unit tests prove string output, but only a tmux capture proves glyphs, width, and
colour survive the real pane. Rejected alternative: folding docs into N04's PR — that makes the
stack's largest-behaviour PR also carry prose churn, and leaves no node positioned after all
rendering work to run the cross-seam replay. A dedicated top-of-stack node closes the
deliverable last, so the milestone completing coincides with the KR being observed.

## Completion

- **Done when:** `README.md` "Inspecting a live run" contains a fenced sample of the new
  transcript rendering, noting cost is footer-only.
- **Done when:** the stale `design-decisions.md` sentence describes the display-side renderer
  downstream of `tee` instead of claiming raw passthrough.
- **Done when:** the replay shows the captured session as ⏺/⎿/✻ transcript lines in a real tmux
  pane while the FIFO copy is byte-identical to the input fixture.

## Assumptions

- A representative stream-json session capture is cheap to produce (one short `claude -p --verbose --output-format stream-json` run, or the `tee` copy of any drain). *(verified — the FIFO branch is exactly this capture)*
- The throwaway-tmux replay technique works headlessly: `tmux new-session -d`, pipeline string, `capture-pane -p`. *(verified — exercised during the investigation that produced this plan)*

## Key Risks

- **Risk:** the README sample is hand-typed and drifts from real output immediately.
  *Mitigation:* paste the sample from the replay's `capture-pane` output rather than authoring
  it.

## Tasks

- [ ] `skeleton` — README sample (pasted from a real replay capture) plus the design-decisions sentence fix · Done when: both docs render the new reality and the sample matches capture-pane output verbatim · Model: Fast · risk reversible · review standard · axes RC·SC·HS·SR·OR = L·L·L·L·L
- [ ] `acceptance` — Replay the captured session through the real pane pipeline in a throwaway tmux session · Done when: capture-pane shows transcript rendering and the FIFO copy is byte-identical to the fixture (closes D2/KR1; close this last) · Model: Balanced · risk reversible · review standard · axes RC·SC·HS·SR·OR = M·M·L·L·L
