# AGENTS.md

Instructions for any coding agent (Claude Code, Codex, etc.) working in this repository. Tool-agnostic by design — see `CLAUDE.md` for any Claude-Code-specific additions.

## What this repo is

`drain-cycle` is a CLI that picks up a well-scoped Linear cycle and executes its issues unattended, spawning a fresh `claude -p --dangerously-skip-permissions` session in an isolated worktree per issue. Python, single-user, personal product.

**Guiding vision — read first: [`docs/vision.md`](docs/vision.md).** It is the project's north-star: stop being the thing that holds the work together, and build that thing once. Every decision, project, and ticket aligns to it; every analytics review and retro is grounded in it. Propose work against the vision; when a change conflicts with it, raise that explicitly rather than drifting.

**Architecture that serves it: [`docs/architecture.md`](docs/architecture.md).** drain-cycle is the automated supervisory layer (Layer 1) over a pack of composable skills (Layer 2), split on an artifact boundary, so the same skills run by hand or unattended. Read it before making architectural changes.

The design rationale (decisions taken, alternatives considered, kill condition) lives in `docs/design-decisions.md` — the running decision log, made in service of the vision above. Read both before making architectural changes.

## Linear workflow

Linear is authoritative for status. Local task lists are fine for within-session bookkeeping; they don't replace a Linear issue.

**Project:** https://linear.app/ababushkin/project/autonomous-cycle-drain-eliminate-manual-shepherding-75daa8863063 (team ABA / Personal).

**Cycles.** Work is planned across cycles, often spanning multiple projects at once. When picking up an issue, prefer ones already in the current cycle. If you start something not in the cycle, decide explicitly whether to pull it in or defer — don't silently expand cycle scope. Use `mcp__linear-server__list_cycles` to see the current cycle.

**On start of any issue:**
- Move to **In Progress** via `mcp__linear-server__save_issue`.
- If the issue isn't yet in the current cycle and you intend to ship it this cycle, assign it to the current cycle.

**On completion:** Before an issue moves to **Done**, all of these must pass in order:

1. **Review** — Run `/code-review-and-quality` against the working-tree changes.
2. **Fix** — Address any Critical or Required findings. Lower-severity findings are at the agent's discretion (fix or note in the summary comment).
3. **Commit + push** — The path here branches by how the issue is being executed:
   - **Direct mode** (interactive session, not spawned by drain-cycle): commit the reviewed version and push to main. This repo pushes directly to main; PRs only when the owner asks. An issue isn't Done if work only exists locally.
   - **Drain mode** (spawned by the drain-cycle orchestrator in a worktree): commit the reviewed changes to the issue branch as reviewable slices (one logical change per commit), then run `/shape:pr-finishing`. That skill owns submission — it drives `gt`/`gh` to submit the stacked PR(s), writes the submitted PR URLs into `.drain-handoff.json` (`pr_urls`), and posts the review-summary comment on the Linear issue. The worker does not run `gt`/`gh` by hand, does not push by hand, and does not hand-author `.drain-handoff.json`. The orchestrator no longer assembles or submits anything; it reads `pr_urls` back as its confirmation that submission succeeded — and a Done stack-mode issue with no `pr_urls` halts the run rather than letting the next issue stack on an unpushed branch. When the prompt names a base branch other than `main` (the orchestrator chains consecutive same-repo issues so each worktree branches off the prior issue's branch), pass that base to `/shape:pr-finishing` so it slices `<base>..HEAD` rather than `main..HEAD`.
4. **Summary comment** — In drain mode the `pr-finishing` skill posts the review-summary comment (findings by severity plus the submitted PR links) as part of submission — the worker does not post a second comment. In direct mode, post a short review-summary comment on the issue via `mcp__claude_ai_Linear__save_comment`.
5. **Done** — Transition to Done via `mcp__claude_ai_Linear__save_issue`.

Status updates happen at the moment of state change — not batched at end of session.

**Blocked** = leave In Progress + add a blocker comment naming the blocker. Don't silently park work.

**New work surfaced mid-flight** becomes a new Linear issue, slotted into a cycle deliberately. Don't silently expand scope.

## Comments

A code comment explains the code **as it stands** — an invariant, a non-obvious constraint, a workaround the next reader would otherwise be surprised by. It does not narrate how the code came to look this way.

Do not write, in any comment or docstring:

- Linear issue / ticket IDs (`ABA-NNN`), user-story labels (`US-A`…`US-D`), or task numbers (`Task 3`, `AC2`).
- Commit SHAs or PR numbers.
- Fix-history narration: "added for…", "fixes the bug where…", "regression caused by…", "previously this…", dates an issue was discovered.

That context belongs in the commit message, the PR description, the Linear issue, or `docs/design-decisions.md` — durable artefacts that carry process history without rotting into the code. A comment pointing at a closed ticket or a squashed commit is noise to everyone who reads the code later.

When you find an existing comment that breaks this: strip the reference and keep the explanatory prose; reword if the label was the grammatical subject; inline the explanation if it lived inside the parenthetical; delete the whole comment if nothing of value remains.

Not covered by this rule — leave these alone: identifiers used as **sample data** in tests (`_issue("ABA-1", …)`, `worktree_path=".../ABA-A"`) and comments describing that data; schema/format placeholders (`"issue_identifier": "ABA-NNN"`); and references to in-repo docs (`README §1`, `docs/design-decisions.md`).

## Git

Conventional-commit-ish prefixes: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`. Subject line ≤ 70 chars; details in the body if needed. Do not add `Co-Authored-By` trailers.
