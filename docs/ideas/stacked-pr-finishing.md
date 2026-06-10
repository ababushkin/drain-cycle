# Slice-During-Build PR Finishing

## Problem Statement
**How might we** let each completed task land as a set of small, well-described, stacked
PRs — without the orchestrator owning fragile `gt`/`gh` invocations that halt the whole run
on any error?

## Recommended Direction
**Direction B: slice-during-build + a recovery-aware finishing skill.**

The root cause is that Linear issues are too coarse — one issue legitimately contains 3–5
independent changes. Two failed/rejected ways to fix this:
- **Pre-shaping** (shape:* skills cutting PRs at *planning* time) — too hard; you can't predict
  the right PR cuts before building.
- **Post-hoc re-slicing** (split the finished flat diff) — arguably harder; reconstructing
  independently-green commit boundaries from a squashed diff is error-prone.

B hits the sweet spot: **decide PR boundaries during the build, when the agent has the most
context and each slice is naturally green.** The worker commits in reviewable slices (one
logical change per commit). A final finishing skill then maps commits → a stacked set of PRs,
fills a What/Why/Focus body per PR, drives `gt` itself inside a **recovery loop** (read stderr,
re-track, rebase, detect missing `gt auth`/`gt init` preconditions), and only emits a halt
signal when genuinely stuck. `graphite.py` in the orchestrator shrinks toward zero — the agent,
not Python, holds the graphite knife, with a recovery loop between the commands and the halt.

This keeps your "correctness > throughput" stance: real, unrecoverable failures still stop the
line; transient gt friction no longer does.

## Relationship to ABA-366 (N08)
ABA-366 covers ~40% of this: it moves PR finishing into a skill, nails the What/Why/Focus body,
and posts the Linear trail. It does **not** cover (a) decomposing one coarse issue into multiple
small PRs — its "stacked" means stacked across issues, not splitting one issue; (b) slice-during-
build; (c) the retry-then-halt recovery loop (its resilience story is a git fallback for
gt-less machines, which is unrequested scope). N08 should either be expanded or paired with a
decomposition sibling, or you'll ship better-described fat PRs.

## Key Assumptions to Validate
- [ ] Worker-time commit discipline is reliable — *test:* run a real multi-change issue and check
      the worker produces clean, independently-green slices without heavy prompting.
- [ ] An agent can drive `gt track`/`gt submit` in a recovery loop more robustly than Python —
      *test:* inject a stale-parent / dirty-tree failure and confirm the skill recovers.
- [ ] Each slice builds + passes tests on its own (reviewer can review PR-by-PR) — *test:*
      `gt submit` each slice and run the suite at each stack level.
- [ ] Missing `gt auth`/`gt init` preconditions can be detected and reported clearly rather than
      surfacing as a cryptic submit abort — *test:* run in a repo without `gt init`.

## MVP Scope
**In:** worker prompt + handoff schema updated to commit in reviewable slices; a finishing skill
that maps slices → stacked PRs with What/Why/Focus bodies; a recovery loop around gt/gh that
halts only when unrecoverable; review-summary comment + Linear status (port from N08).
**Out:** plain-git fallback for gt-less environments; auto-`gt auth`; pre-shaping at planning time.

## Not Doing (and Why)
- **Pre-shaping PR cuts at planning time** — proven too hard; boundaries belong at build time.
- **Post-hoc re-slicing of a flat diff** — harder than slicing during build; only a fallback.
- **Keeping gt in orchestrator Python (harden-in-place)** — doesn't address fat PRs at all; you
  ruled out this owner.
- **Plain-git fallback (N08's gt-less path)** — unrequested scope; single-user, gt is present.

## Open Questions
- Does the worker commit-in-slices discipline survive `--dangerously-skip-permissions` without a
  human nudging boundaries? If not, the post-hoc fallback (A) becomes load-bearing.
- Should the finishing skill be one skill that supersedes/absorbs ABA-366, or a decomposition
  skill that runs *before* N08's body+trail step?
