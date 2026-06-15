---
layer: node
id: N01
type: story
title: Finishing sub-agent recovers committed work
parent: D1
serves_kr: KR1
maps_to: linear-issue
skeleton: true
external_window: none
completion:
  form: acceptance-criteria
  criterion: >
    When a worker exits with commits beyond base but the issue is not properly closed, the
    orchestrator spawns a sonnet finishing sub-agent, re-checks Linear state plus
    .drain-handoff.json, and either proceeds on success or halts with a recovery-attempted reason —
    on both the not-Done and stack-no-PRs paths. An empty or uncommitted-only branch halts
    untrusted.
delegates_to: at-pickup task breakdown (story)
---

# N01 — Finishing sub-agent recovers committed work

**Type:** `story`. The plan's walking skeleton: its first task exercises the real risky seam — the
orchestrator spawning a nested `claude -p` finishing session and re-reading completion state from
Linear and the worktree — so integration risk surfaces on day one rather than at wiring time.

## What

As the drain-cycle operator running an unattended cycle, I want the orchestrator to drive a
committed-but-unfinished issue to completion through a fresh finishing sub-agent before it halts, so
that a weak worker that commits its slices but skips the finishing protocol no longer strands
reviewable work or stops the run.

The seam lives in `_drain_one_issue` (`drain_cycle/orchestrator.py`), at the two terminal halt
sites: the not-Done halt (`:781`, slug `err-issue-not-done`) and the stack-no-PRs halt (`:706`,
slug `err-stack-no-prs`). Before either reverts and records, the orchestrator inspects the worktree
branch. With commits beyond base (`git rev-list <base>..HEAD --count > 0`) and the issue not
properly closed, it spawns a finishing-only sub-agent — reusing `worker.run_issue`
(`worker.py:159`) with a new `prompt.build_finishing` (alongside `_stack_preamble` /
`_resume_directive` in `prompt.py`) — then re-reads `linear.get_issue` and `handoff.read`
(`handoff.py:49`). On success it joins the existing happy path (`:733`): extend the baton, record
submitted PRs, tear down. On failure it falls through to the unchanged halt, now naming that
recovery was attempted.

## Why

The bet: completeness is the orchestrator's contract to enforce, not the worker's to be trusted
with. The orchestrator already reads the proof signals (`final_linear_state`, `handoff.read`); the
only missing move is to act on a recoverable gap instead of halting on it. The finishing sequence
is mechanical and was proven by hand on ABA-383, so a fixed sonnet sub-agent runs it reliably
regardless of the issue's `model:` label, while any Critical/Required code fix — the part that
needs capability — delegates to opus.

Rejected alternative: have the orchestrator itself run `gh`/`gt` and post the Linear comment. That
moves submission logic back into the orchestrator that AGENTS.md deliberately stripped it from, and
duplicates the `/shape:pr-finishing` discipline. The sub-agent keeps submission where the contract
puts it. Also rejected: trust uncommitted working-tree changes — completeness of an uncommitted
tree is unknowable (50%? 90%?), so an empty or uncommitted-only branch stays a genuine failure.

## Completion

- **Done when:** on the not-Done path, an issue left with commits beyond base and state ≠ Done
  triggers a sonnet finishing sub-agent; after it exits, a re-check that reads Done is treated as
  success and the run continues, and a re-check still not Done halts with a reason naming the
  attempted recovery.
- **Done when:** on the stack-no-PRs path, an issue marked Done with commits beyond base but no
  `pr_urls` triggers the same finishing sub-agent; a re-check that reads non-empty `pr_urls` extends
  the baton, and a still-empty `pr_urls` halts.
- **Done when:** an issue whose branch has no commits beyond base, or whose only changes are
  uncommitted, halts with the existing reason and spawns no finishing sub-agent.
- **Done when:** the finishing sub-agent runs on `claude-sonnet-4-6` and its prompt instructs it to
  delegate Critical/Required fixes to `claude-opus-4-7`; at most one finishing attempt runs per
  issue per run; and a finishing spawn is recorded as its own run-log entry.

## Assumptions

- `worker.run_issue` can be invoked a second time within one `_drain_one_issue` call against the same worktree without orchestrator-side state collision. *(to-verify)*
- The worktree still exists and its branch is intact at both halt sites — teardown runs only on the happy path, after the gate. *(verified)*
- `handoff.read` returning non-empty `pr_urls` is a sufficient submission-success signal for the recovered stack-no-PRs case, identical to the first-pass gate. *(verified)*
- The base branch for `<base>..HEAD` is recoverable at the halt site from the same source the worker prompt used — chained base or `main`. *(to-verify)*

## Key Risks

- **Risk:** the finishing sub-agent loops or re-strands work, turning one halt into repeated spawns.
  *Mitigation:* a one-attempt-per-issue-per-run guard caps finishing spawns; `max_resume_attempts`
  stays as the cross-run backstop.
- **Risk:** the finishing sub-agent re-implements rather than finishes, mutating the committed diff.
  *Mitigation:* the `build_finishing` prompt states the work is already committed and forbids
  re-implementation, scoping the agent to review → fix → submit → Done. *Falsifier:* a finishing
  run whose post-spawn `<base>..HEAD` diff adds non-review commits beyond the fix.
- **Risk:** detecting "committed beyond base" misfires when the base itself is ambiguous, spawning a
  finishing agent against the wrong range. *Mitigation:* resolve `<base>` from the same value the
  worker prompt was built with and assert it is an ancestor of HEAD before spawning.

## Tasks

- [ ] `skeleton` — On the not-Done halt path in `_drain_one_issue`, detect commits beyond base, add `prompt.build_finishing`, spawn a sonnet finishing sub-agent via `worker.run_issue`, and re-check `linear.get_issue` + `handoff.read`, recovering to the happy path on Done or falling through to the existing halt (real nested `claude -p` spawn against the worktree, not a stub) · Done when: an issue exited not-Done with commits beyond base runs the finishing sub-agent and either reaches Done-and-continues or halts with a recovery-attempted reason, exercised end-to-end against a real worktree · Model: Frontier · risk reversible · review standard · axes RC·SC·HS·SR·OR = H·M·M·M·H
- [ ] Extend the same recovery to the stack-no-PRs halt path (`err-stack-no-prs`), where an issue Done with commits beyond base but empty `pr_urls` runs the finishing sub-agent and a re-check with non-empty `pr_urls` extends the baton · Done when: a Done-but-no-`pr_urls` issue with commits beyond base is recovered to non-empty `pr_urls` and extends the baton, or halts unchanged when recovery fails · Model: Balanced · risk reversible · review standard · axes RC·SC·HS·SR·OR = M·M·L·M·M
- [ ] Harden and bound the recovery — run the finishing sub-agent on `claude-sonnet-4-6` with a prompt that delegates Critical/Required fixes to `claude-opus-4-7`, guard one finishing attempt per issue per run, write a run-log entry for the finishing spawn, and enrich the fall-through halt reason · Done when: a second finishing attempt within one run is refused, the finishing spawn appears as its own run-log entry, and the prompt pins sonnet with an opus fix-delegation instruction · Model: Balanced · risk reversible · review standard · axes RC·SC·HS·SR·OR = M·M·L·M·M
