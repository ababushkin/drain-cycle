# Triage record: live-execution-swimlanes

## Raw intake
<!-- Verbatim capture of the idea as received. Do not edit. -->
swim lanes to show me which "agents" are doing what during the execution run

for example i want to be able to see something like:

ABA-313: Issue Picked Up
--| Starting supervising agent
--| [Code is written]
----| Starting code-review sub-agent
----| Starting code-simplification sub-agent

etc

Basically it should follow the architecture and personas I have but I want to be able to see what's going on while drain-cycle is running

This is equivalent to a kanban board, but in a vertical UI rather than a horizontal Kanban

## Refined intent
<!-- The confirmed six-line restate from the elicitation loop, in the user's own words. -->
- Outcome: The default drain-cycle run output shows a live, vertically-nested tree of the agent hierarchy for the issue in flight — supervisor → current `exec:*` skill step → sub-agent persona — with the active node visibly marked.
- User: The operator running a drain-cycle, who wants to trust the run without babysitting it.
- Why now: The current default output is a flat, timestamped event stream plus a token line; it never shows *where in the standard chain* the work is, so the run reads as a black box.
- Success: At a glance the operator can tell which step/persona is executing and what is done vs upcoming — confidence the run is following the steps. (Not whether output is streaming — that is the `-w` watch pane.)
- Constraint: This is the non-`-w` default output; issues run one at a time (sequential); the tree must reflect the real `exec:*` chain and personas, at **step + persona depth**.
- Out of scope: The `-w` watch pane (raw live agent streaming); parallel/multi-issue swim lanes (execution is sequential today).

**Assumptions surfaced:**
- This is a distinct surface from the `-w` watch pane. `-w` shows raw streaming from a single agent; this view shows the *structure* of the run (which step/persona is active), at step + persona depth.
- Execution is sequential — one worker / one `active.json` marker at a time — so "swim lanes" here means the nested agent tree *within* the current issue, not concurrent lanes across issues.
- **(Feasibility — the spike rests on this.)** Rendering step + persona depth requires drain-cycle to know which `exec:*` skill and which review persona the worker is in. The worker is a single agent that invokes the skills internally; today the orchestrator consumes the worker's event stream for turns/tokens but has no structured notion of the active step. The view is only possible if that stream exposes Skill-invocation and sub-agent-spawn events cleanly, or if a lightweight marker contract is added. The chosen product spike assumes this is solvable and designs the UX on top of it; the spike should sanity-check the assumption before investing in layout.

**Open questions:**
- Does the view replace the flat event stream in the default output, or augment it (tree as a header/status region, events still scrolling beneath)?
- How is "done vs current vs upcoming" rendered for the expected chain — pre-drawn full chain with state per node, or progressively revealed?
- Is a static-on-update render enough, or does it need a live-refreshing region (and how does that coexist with the scrolling worker `│` output)?

## Problem restatement
<!-- "For [customer segment], we believe [problem] is causing [negative outcome]." -->
For the drain-cycle operator, we believe the default run output's flat event stream — which never shows which `exec:*` step or persona is active — is causing the run to read as a black box, so the operator cannot trust it without babysitting.

Original framing was a solution ("swim lanes / vertical kanban"). Restated to the underlying problem: the run's structure is invisible during execution, which undermines trust (the confirmed underlying job).

## Evidence
<!-- What evidence exists that this problem is real and affects the named customer?
     Be specific: quote, data point, observation. Name the source. -->
The operator's own stated want, expressed during intake ("I want to be able to see what's going on while drain-cycle is running"). No usage data, no logged friction, no second observer.

`docs/app-context.md` is absent — there is no baseline metric to ground this against. The assessment is **ungrounded**.

Confidence score (Gilad): **0.5** — anecdote / one-off observation (a single person's stated preference, not data).

## Routing
<!-- idea bank | validation slot -->
**Validation slot.** Confidence 0.5 is well below the build-bet threshold (≥ 5), so this earns a validation slot, not a build slot (Rule B6).

- **Validation method:** `shape:design` — **product spike**.
- **Dominant unknown:** Product feel — what the vertical tree should look like and how it coexists with the existing default output (replace vs augment, live-refresh vs static, done/current/upcoming rendering). The riskiest thing is not whether it can be built but whether the chosen layout actually delivers the trust-at-a-glance outcome.
- The product spike rests on the feasibility assumption recorded above (the worker stream must expose step/persona transitions). The spike should confirm that assumption is plausible before investing in layout, but the layout is the unknown being resolved.

## Notes
<!-- Anything a future reader needs: related items, strategic context. -->
- Distinct from the `-w` watch-pane upgrade (ABA-386–390): that surface shows raw live agent streaming; this surface shows run *structure* / which step+persona is active. They are complementary, not the same work.
- Architecture grounding: the `exec:*` chain is `pickup → breakdown → build → review → verify → simplify → finish`, with `exec:debug` as escalation; `exec:review` fans out into personas (spec-compliance, security-auditor, code-quality). The tree must mirror this real chain, not an invented one. See `docs/architecture.html`.
- Current output code: `drain_cycle/console.py` (flat `orch` / `ABA-NNN` / `HALT` event lines + `│`-indented agent output) and `drain_cycle/progress.py` (`format_progress_line`, `active.json` marker). A swimlanes view would build on or replace this rendering layer.
