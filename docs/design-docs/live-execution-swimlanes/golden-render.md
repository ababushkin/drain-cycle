# Golden render — live execution swimlanes (D2 acceptance target)

The visual acceptance target for milestone **D2** (issues N04–N11). Each D2
slice asserts its part of these frames; a final assembly check asserts a whole
frame. These frames define what "done" looks like, so a worker validates its
render against a fixed reference instead of against prose.

We hand-authored this from the design doc's render section; we did **not**
generate it from `docs/prototypes/live-execution-swimlanes/demo.py`. The demo
renders with `rich.Live` and drills further into each step than the design
commits to. The frames here match production. Where the demo and the design
disagree, the design wins, and so does this file. See
[Divergences from the demo](#divergences-from-the-demo).

## How to use it

- **Per-slice tests.** Each D2 node renders one part of a frame. Drive the
  renderer with the [scene state](#the-scene) below, capture the owned region,
  and assert the lines this file marks as that node's. N04 owns the spine line;
  N08 owns the queue rows; N09 owns the footer; N11 owns the persona rows; and so
  on (see the [ownership map](#ownership-map)).
- **Assembly check.** One test drives the full scene and asserts a whole frame
  byte-for-byte, after the [comparison rule](#comparison-rule).
- **The frames are the fixture.** Copy a frame into a `.txt` fixture beside the
  test, or build it from the [column rules](#column-rules). When a counted space
  and a rule disagree, the rule wins.

### Comparison rule

Compare **per line, trailing whitespace stripped**. Production positions the
owned region absolutely and clears to end of line, so it never emits trailing
padding; the column-pad widths below set where a field *starts*, not its
trailing spaces. Assert colour separately against the [style map](#style-map);
these plain-text frames do not carry it.

### Glyph legend

| Glyph | Meaning | Used in |
|-------|---------|---------|
| `●` | done | queue lane, stepper spine |
| `◉` | active / running | queue lane, stepper spine |
| `○` | queued / upcoming | queue lane, stepper spine |
| `✓` | persona GO | persona drill-down |
| `▶` | running / focus marker | persona row, queue focus, active-step head |
| `·` | not-yet-run | persona row |
| `✗` | persona finding (NO-GO) | persona row |
| `━━` | step connector | stepper spine |

## The scene

A four-issue cycle, **ABA-FEAT · reliable webhooks**, in dependency-resolved
execution order (topo sort over `blocked_by[]`):

| # | Issue | Title | Depends on | State in the canonical frame |
|---|-------|-------|-----------|------------------------------|
| 1 | ABA-301 | Add webhook retry queue table | — | done · PR …/451 |
| 2 | ABA-302 | Retry sender with capped backoff | ABA-301 | running · `review` |
| 3 | ABA-303 | Emit retry metrics + dashboard | ABA-302 | queued |
| 4 | ABA-304 | Document retry behaviour + runbook | ABA-302 | queued |

ABA-302 carries the failure path: its `review` reports a security finding, loops
back to `build` on a NO-GO verdict, then re-reviews clean. That gives every
state below its own frame.

---

## Frame A — running issue, review mid-fan-out (canonical)

Focus follows the running issue (ABA-302). `review` is active: spec-compliance has
cleared, security-auditor is mid-review, code-quality has not started.

```
   cycle ABA-FEAT · reliable webhooks   1/4 done · dependency-resolved execution order

   1 ● ABA-301  Add webhook retry queue table        done · github.com/acme/app/pull/451
  ▶ 2 ◉ ABA-302  Retry sender with capped backoff      running · review
   3 ○ ABA-303  Emit retry metrics + dashboard        queued · after ABA-302
   4 ○ ABA-304  Document retry behaviour + runbook     queued · after ABA-302

  ────────────────────────────────────────────────────────────────

   ABA-302   Retry sender with capped backoff

  ●━━●━━●━━◉ review ━━○━━○━━○
  pickup     breakdown  build      review     verify     simplify   finish

  ▶ review   Multi-persona review over the diff → one deduped GO/NO-GO verdict
      ✓ spec-compliance               GO · meets ac_checklist
      ▶ security-auditor              reviewing diff…
      · code-quality

  turn 47 · 312k tok (peak 89k) · 18m22s

  ↑/↓ or 1-4 select · f follow (on) · q quit
```

## Frame B — NO-GO verdict flash

One transition later: security-auditor returns a finding, the aggregate verdict
is NO-GO, and the flash line announces the loop-back. The flash (N05) is an
append-safe line above the swimlane footer.

```
   cycle ABA-FEAT · reliable webhooks   1/4 done · dependency-resolved execution order

   1 ● ABA-301  Add webhook retry queue table        done · github.com/acme/app/pull/451
  ▶ 2 ◉ ABA-302  Retry sender with capped backoff      running · review
   3 ○ ABA-303  Emit retry metrics + dashboard        queued · after ABA-302
   4 ○ ABA-304  Document retry behaviour + runbook     queued · after ABA-302

  ────────────────────────────────────────────────────────────────

   ABA-302   Retry sender with capped backoff

  ●━━●━━●━━◉ review ━━○━━○━━○
  pickup     breakdown  build      review     verify     simplify   finish

  ▶ review   Multi-persona review over the diff → one deduped GO/NO-GO verdict
      ✓ spec-compliance               GO · meets ac_checklist
      ✗ security-auditor              FINDING · unbounded retry → DoS risk
      ✓ code-quality                  GO · clear, no smells

  verdict: NO-GO — security finding → looping back to build

  turn 52 · 341k tok (peak 89k) · 19m41s

  ↑/↓ or 1-4 select · f follow (on) · q quit
```

## Frame C — viewing a queued issue (toggle off the running one)

The operator pressed `4` (or `↓`) to inspect ABA-304 while ABA-302 keeps running.
Auto-follow is now off. The queue still shows ABA-302 as running (`◉`); only the
focus marker `▶` and the drawn swimlane move. A queued issue's swimlane shows the
all-upcoming spine and what it waits on. It has not run, so it shows **no footer
and no flash**.

```
   cycle ABA-FEAT · reliable webhooks   1/4 done · dependency-resolved execution order

   1 ● ABA-301  Add webhook retry queue table        done · github.com/acme/app/pull/451
   2 ◉ ABA-302  Retry sender with capped backoff      running · review
   3 ○ ABA-303  Emit retry metrics + dashboard        queued · after ABA-302
  ▶ 4 ○ ABA-304  Document retry behaviour + runbook     queued · after ABA-302

  ────────────────────────────────────────────────────────────────

   ABA-304   Document retry behaviour + runbook

  ○━━○━━○━━○━━○━━○━━○
  pickup     breakdown  build      review     verify     simplify   finish

  ○ queued — not started yet · waits on ABA-302

  ↑/↓ or 1-4 select · f follow (off) · q quit
```

## Frame D — viewing a done issue

The operator toggled to ABA-301, already finished. The spine is all done. No step
is active, so there is no drill-down, and the completed flash shows the PR.

```
   cycle ABA-FEAT · reliable webhooks   1/4 done · dependency-resolved execution order

  ▶ 1 ● ABA-301  Add webhook retry queue table        done · github.com/acme/app/pull/451
   2 ◉ ABA-302  Retry sender with capped backoff      running · review
   3 ○ ABA-303  Emit retry metrics + dashboard        queued · after ABA-302
   4 ○ ABA-304  Document retry behaviour + runbook     queued · after ABA-302

  ────────────────────────────────────────────────────────────────

   ABA-301   Add webhook retry queue table

  ●━━●━━●━━●━━●━━●━━●
  pickup     breakdown  build      review     verify     simplify   finish

  completed · PR github.com/acme/app/pull/451

  turn 39 · 198k tok (peak 76k) · 14m05s

  ↑/↓ or 1-4 select · f follow (off) · q quit
```

## Frame E — cycle complete (the literal "done" state)

Every issue is done, nothing is running. The header reads `4/4 done`, every lane
is `●`, and focus rests on the last issue. The operator sees this when they step
away and return to a finished run.

```
   cycle ABA-FEAT · reliable webhooks   4/4 done · dependency-resolved execution order

   1 ● ABA-301  Add webhook retry queue table        done · github.com/acme/app/pull/451
   2 ● ABA-302  Retry sender with capped backoff      done · github.com/acme/app/pull/452
   3 ● ABA-303  Emit retry metrics + dashboard        done · github.com/acme/app/pull/453
  ▶ 4 ● ABA-304  Document retry behaviour + runbook     done · github.com/acme/app/pull/454

  ────────────────────────────────────────────────────────────────

   ABA-304   Document retry behaviour + runbook

  ●━━●━━●━━●━━●━━●━━●
  pickup     breakdown  build      review     verify     simplify   finish

  completed · PR github.com/acme/app/pull/454

  turn 44 · 221k tok (peak 81k) · 12m38s

  ↑/↓ or 1-4 select · f follow (on) · q quit
```

## Frame F — build active, debug escalation inline

A non-review active step shows a **one-line sub-status**, not a row list (see
[Divergences](#divergences-from-the-demo)). `debug` appears inline in the spine
only while entered: it is an escalation, not a fixed slot.

```
   cycle ABA-FEAT · reliable webhooks   1/4 done · dependency-resolved execution order

   1 ● ABA-301  Add webhook retry queue table        done · github.com/acme/app/pull/451
  ▶ 2 ◉ ABA-302  Retry sender with capped backoff      running · build
   3 ○ ABA-303  Emit retry metrics + dashboard        queued · after ABA-302
   4 ○ ABA-304  Document retry behaviour + runbook     queued · after ABA-302

  ────────────────────────────────────────────────────────────────

   ABA-302   Retry sender with capped backoff

  ●━━●━━◉ build ━━◉ debug ━━○━━○━━○━━○
  pickup     breakdown  build      review     verify     simplify   finish

  ▶ build   RED → GREEN → commit per slice
      red loop stuck → exec:debug · root cause: off-by-one in backoff

  turn 31 · 142k tok (peak 71k) · 9m12s

  ↑/↓ or 1-4 select · f follow (on) · q quit
```

---

## Column rules

Authoritative if a counted space and a rule disagree. Every line is prefixed with
two spaces (the region's left gutter).

**Queue header**
```
"  " + " cycle <cycle-id> " + "  {done}/{n} done · dependency-resolved execution order"
```
The `" cycle … "` badge is reverse-video; its surrounding spaces are part of the
highlight.

**Queue row**
```
"  " + focus + idx + lane + id + title + "   " + status
       focus  = "▶ " when this row is the focused/viewed issue, else "  "
       idx    = f"{position} "                 # 1-based pick order
       lane   = f"{● | ◉ | ○} "                # done | running | queued
       id     = f"{identifier}  "              # two trailing spaces
       title  = f"{title:<W}"                  # W = max title width in the cycle
       status = done    → f"done · {pr_url}"
                running → f"running · {active_step}"
                queued  → f"queued · after {', '.join(blocked_by)}"  (or "queued · ready")
```
Rows render in the orchestrator's pick order and are **never** re-sorted by the
renderer.

**Separator**: `"  " + "─" * 64`.

**Swimlane header**: `"  " + " {identifier} " + "  {title}"` (the `" {id} "` badge
is reverse-video).

**Stepper spine** — over the canonical chain
`pickup → breakdown → build → review → verify → simplify → finish`:
```
for each step:  glyph(done ● | active ◉ | upcoming ○)
                + " {step} "   when the step is active
                + "━━"         between steps (not after the last)
```
`debug` is inserted inline next to `build` only while entered; `simplify` is shown
as a normal chain slot but is reached only on the green path. Stream names
(`exec:pickup`) and marker names (`pickup`) normalize to the same slot.

**Step-name row**: `"  " + "".join(f"{step:<11}" for step in chain)`.

**Active-step head**: `"  ▶ {active}   {caption}"` — caption is the first sentence
of the step's `SKILL.md` `description`, sourced from the pack.

**Persona drill-down** (review only): `"      {mark} {persona:<30}{note}"`, six-space
indent, mark in `✓ ▶ · ✗`.

**Non-review active sub-status**: `"        {one-line activity}"`, eight-space indent.

**Verdict / completion flash**: `"  {message}"`, a standalone line above the footer.

**Footer**: `"  turn {turn} · {tokens} tok (peak {peak}) · {elapsed}"`.

**Help line**: `"  ↑/↓ or 1-{n} select · f follow ({on|off}) · q quit"`.

### Format rules

- **Tokens**: `≥1e6 → f"{n/1e6:.1f}M"`; `≥1000 → f"{n//1000}k"`; else the integer.
- **Elapsed**: `≥3600 → f"{h}h{m}m"`; `≥60 → f"{m}m{s:02d}s"`; else `f"{s}s"`.

## Style map

Plain text can't carry colour; assert it separately.

| Element | Style |
|---------|-------|
| done glyph / done lane / done step name | green |
| active/running glyph / spine / active step name | bold cyan |
| queued/upcoming glyph / name | grey |
| focus marker `▶` | bold yellow |
| `" cycle … "` and `" ABA-NNN "` badges | reverse-video |
| persona `✓` GO | green |
| persona `▶` running | bold cyan |
| persona `·` not-run | grey |
| persona `✗` finding | bold red |
| active-step caption | dim italic |
| GO / completion flash | bold green |
| NO-GO flash | bold red |
| footer, help line | dim |

## Ownership map

Which D2 node owns which lines, so a slice asserts only its part of a frame.

| Node | Owns |
|------|------|
| N04 | the full stepper spine and step-name row (done/active/upcoming, `debug` inline) |
| N05 | the verdict / completion flash line (Frames B, D, E) |
| N06 | the focus marker `▶` position and the `follow (on/off)` state in the help line |
| N07 | the owned multi-line region itself — all frames render into it; no corruption under live passthrough |
| N08 | every queue row's content: title, per-state status, and the `{done}/{n}` header |
| N09 | the footer line, including `(peak …)` |
| N11 | the persona drill-down rows (Frames A, B) |

The active-step head + caption and the non-review one-line sub-status (Frame F) are
not yet owned by a named node — see the open gaps in the project review.

## Divergences from the demo

`demo.py` is a throwaway UX illustration; this fixture corrects it to the design:

- **One mechanism, not `rich`.** Production draws the owned region with a DECSTBM
  scroll region (N07). The demo uses `rich.Live`, which N07 rejects because it
  takes over the cursor and suppresses the worker passthrough.
- **Persona drill-down is review-only.** The demo also renders per-task rows under
  `breakdown` and per-slice rows under `build`. The design draws a multi-row
  drill-down only for `review` personas (N11); every other active step shows a
  single one-line sub-status (Frame F).
- **No pause.** The demo binds `space` to pause. A live `claude` cannot pause, so
  N06 drops the binding and the help line shows no `space pause`.
- **No demo provenance line.** The demo prints `reads … · scripted timings, no
  Claude calls`. Production does not.
</content>
</invoke>
