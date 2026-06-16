# AGENTS.md

Instructions for any coding agent (Claude Code, Codex, etc.) working in this repository. Tool-agnostic by design — see `CLAUDE.md` for any Claude-Code-specific additions.

## What this repo is

`drain-cycle` is a CLI that picks up a well-scoped Linear cycle and executes its issues unattended, spawning a fresh `claude -p --dangerously-skip-permissions` session in an isolated worktree per issue. Python, single-user, personal product.

**Guiding vision — read first: [`docs/vision.md`](docs/vision.md).** It is the project's north-star: stop being the thing that holds the work together, and build that thing once. Every decision, project, and ticket aligns to it; every analytics review and retro is grounded in it. Propose work against the vision; when a change conflicts with it, raise that explicitly rather than drifting.

**Architecture that serves it: [`docs/architecture.html`](docs/architecture.html).** drain-cycle is the automated supervisory layer (Layer 1) over a pack of composable skills (Layer 2), split on an artifact boundary, so the same skills run by hand or unattended. Read it before making architectural changes.

The design rationale (decisions taken, alternatives considered, kill condition) lives as ADRs under `docs/adrs/` — one decision per record, made in service of the vision above. [`docs/adrs/README.md`](docs/adrs/README.md) indexes them. Read both before making architectural changes.

## Linear workflow

The Linear lifecycle (cycle model, issue workflow, completion gate), the comment rules, and the git conventions are governed by the **workflow-hooks** pack — injected at session start, full rubric in its `rules/`. drain-cycle adds only the repo-specific points below.

**Project:** https://linear.app/ababushkin/project/autonomous-cycle-drain-eliminate-manual-shepherding-75daa8863063 (team ABA / Personal).

**Commit + push mode.** The completion gate's commit+push step branches by how the issue runs:

- **Direct mode** (interactive session, not spawned by drain-cycle): commit the reviewed version and push to main. This repo pushes directly to main; PRs only when the owner asks.
- **Drain mode** (spawned by the orchestrator in a worktree): commit the reviewed changes to the issue branch as reviewable slices (one logical change per commit), then run `/shape:pr-finishing`. That skill owns submission — it drives `gt`/`gh` to submit the stacked PR(s), writes the submitted PR URLs into `.drain-handoff.json` (`pr_urls`), and posts the review-summary comment on the issue. The worker does not run `gt`/`gh`, push, post the comment, or hand-author `.drain-handoff.json` by hand. The orchestrator reads `pr_urls` back as confirmation that submission succeeded — and a Done stack-mode issue with no `pr_urls` halts the run rather than letting the next issue stack on an unpushed branch. When the prompt names a base branch other than `main` (the orchestrator chains consecutive same-repo issues so each worktree branches off the prior issue's branch), pass that base to `/shape:pr-finishing` so it slices `<base>..HEAD` rather than `main..HEAD`.
