# ADR 0022: PR links are recorded in the run-log and posted to Linear by the orchestrator

**Date:** 2026-06-01
**Status:** Superseded by ADR 0023
**Migrated from:** docs/design-decisions.md §18

> **Superseded by ADR 0023.** This decision assumes the orchestrator assembles the stack, so `graphite.submit` returns the URL and the orchestrator posts the Linear comment. Under ADR 0023 the worker's finishing skill submits and writes `pr_urls` into `.drain-handoff.json`; the orchestrator *reads them back* rather than producing them. The recording intent survives — memory lives in artefacts — but the actor and the source of the URL changed.

After a stack-mode issue is confirmed Done and its branch is assembled into the per-repo Graphite stack (ADR 0020), the orchestrator needs to close the loop: the operator and any future session must be able to find the PR without reading source or re-running `gh`. Memory lives in artefacts.

**Decision.** The orchestrator already holds the PR's URL and number — `graphite.submit` returns them (`gh pr view` runs as the final step of assembly). On a successful submit it:

1. Records `pr_url`, `pr_number`, `review_high` (the flag computed from the Linear label and the handoff findings, the same one that drives the GitHub `review:high` label), and `parent_branch` (the stack parent the branch was tracked under) in the run-log entry alongside the existing usage fields.
2. Posts a comment on the Linear issue via `linear.add_comment` (GraphQL `commentCreate`) with the PR URL and, when flagged, a `review:high` note.

All four run-log fields are additive and default to `null` (push-to-main repos, halted issues, pre-PR run-logs). `grade.py` reads only `cycle_id`, `final_linear_state`, and `exit_code`, so pre-existing run-logs grade unchanged. The Linear comment is non-fatal: any failure is logged to stderr and the drain continues. The PR link is informational, never load-bearing for the drain's control flow.

**Why the submit result, not a post-hoc `gh pr list` lookup.** An earlier draft of this decision had the orchestrator re-query GitHub by head branch after the worker exited — necessary in a design where the *worker* ran `gt submit`. Under ADR 0020's orchestrator-assembles design the lookup is redundant: the assembly step that creates the PR returns its URL and number in the same call, with no second query, no race against GitHub's index, and no dependency on branch-name conventions.

**Why the orchestrator, not the agent.** The agent commits without pushing and writes the handoff file; it never talks to GitHub. The orchestrator is also the only component that can write to the run-log — it owns the `RunLog` object and calls `append_entry`.

**Alternatives considered.**

- *Post-hoc `gh pr list --head <branch>` lookup.* Rejected as redundant once assembly moved into the orchestrator — see above.
- *Always post the PR comment unconditionally (even on halt).* Rejected: a halted issue has no submitted PR (a graphite failure halts before this step). The comment fires only on confirmed Done with a successful submit.
