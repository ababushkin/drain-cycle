# ADR 0020: The Graphite PR-stacking sequence the orchestrator will run (ABA-300 spike)

**Date:** 2026-06-01
**Status:** Superseded by ADR 0023
**Migrated from:** docs/design-decisions.md §16

> **Superseded by ADR 0023.** The orchestrator no longer assembles or submits the stack. The `gt`/`gh` sequence below is now run by the worker's finishing skill, not the orchestrator; the verified commands stay accurate, but the actor is the skill. See ADR 0023.

Decided empirically by ABA-300: the full `gt` + `gh` stacking sequence was driven by hand against this repo with two stacked branches (`spike/aba-300-step-a` off `main`, `spike/aba-300-step-b` off A), producing PRs [#5](https://github.com/ababushkin/drain-cycle/pull/5) and [#6](https://github.com/ababushkin/drain-cycle/pull/6). The four findings below are what the orchestrator (Ticket 2) wires against — verified commands, not guesses.

**(a) `gt` works from a linked worktree; cwd = the per-issue worktree root.** `gt track` and `gt submit` were run from inside `.worktrees/spike-a` and `.worktrees/spike-b` (linked worktrees created with `git worktree add`), and both succeeded. The Graphite metadata DB lives in the shared `.git` dir (`.git/.graphite_metadata.db`), so every linked worktree sees the same stack state — `gt ls` from worktree B even annotates which worktree each branch is checked out in. **The orchestrator runs `gt` from the per-issue worktree root** (ADR 0007's "fresh worktree per issue"), the same cwd the worker already uses. No need to run from the primary checkout.

**(b) Exact command sequence.** Per branch, in its worktree, adopting the already-created branch (don't recreate):

```
gt track --parent <parent>     # parent = main for the base layer; the layer below for each step up
# ... commits land in the worktree as normal ...
gt submit --stack --no-interactive --publish
```

`--no-interactive` is mandatory under automation: without it `gt submit` prompts for PR fields and would hang the headless run (it prints "Running in non-interactive mode. Inline prompts … will be skipped."). `--publish` makes the PRs ready-for-review; **omit it to leave them draft** (bare `gt submit --no-interactive` creates draft PRs). `--stack` submits every layer below the current branch in one call, so submitting from the top branch creates the whole stack with correct bases (verified: #5 base `main`, #6 base `spike/aba-300-step-a`).

**(c) The PR body comes from the commit-message body; empty body → fall back to `gh pr edit --body-file`.** `gt` populates the PR title from the commit subject and the PR **body from the commit message body** (everything after the subject's blank line) — verified: PR #5's commit carried a `## What / ## Why / ## What to review` body and it appeared **verbatim** in the PR. A commit with only a subject and no body yields an **empty** PR body (verified: PR #6 came up blank). So the orchestrator's rule: **put the What/Why/What-to-review block in the commit message body**; if a layer's body is empty or needs editing after submit, set it with `gh pr edit <n> --body-file <f>` (verified fallback). Labels are applied the same way: `gh label create review:high --color B60205` (idempotent; create-if-absent) then `gh pr edit <n> --add-label review:high`.

**(d) Per-repo preconditions: `gt auth` + `gt init --trunk main`, both one-time and manual.** `gt auth` is an interactive browser flow and **cannot run under `--dangerously-skip-permissions`** — treat it as one-time operator setup, not an automatable step. Likewise `gt init --trunk main` is run once per repo. The orchestrator must **not** attempt either: it assumes both are already done (token in `~/.config/graphite/auth`, trunk config in `.git/.graphite_repo_config`). If `gt auth` or `gt init` is missing, `gt submit` aborts before any push — that is a **stop-the-line**: surface it for the operator, do not work around it.

**Restack conflict policy (stop-the-line, not auto-merge).** A branch forked off an older `main` shows "needs restack"; `gt restack` rebases it. A restack that hits a **semantic conflict is a stop-the-line**: abort with `git rebase --abort` (`gt abort` refuses in non-interactive mode — use the `git` form), leave the stack on its pre-restack history, and surface the conflict for a human. Never auto-resolve and `gt restack --continue`. (This is the rule the `graphite-stack-review.md` runbook §4 was reconciled to.)
