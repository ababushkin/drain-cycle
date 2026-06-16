# ADR 0014: Headless workers inherit project-scoped config by symlink

**Date:** 2026-05-24
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §10

The operator noticed entire.io's checkpointing didn't take effect during a headless drain. The hypothesis was that the worker's worktree cwd (`.worktrees/<id>`) diverges from an interactive session at the repo root. A reproduction run confirmed the divergence and sharpened the cause (below); the resolution is to symlink the repo's project-scoped config into each worktree at spawn, restoring parity.

**What was observed (claude 2.1.150).** Running `claude -p --debug-file <path>` from the repo root vs. from a fresh `git worktree` of the same repo, then diffing the debug logs:

| | Repo root | Worktree |
|---|---|---|
| Settings files watched | user `~/.claude/settings.json` + **project** `.claude/settings.json` + `.claude/settings.local.json` | **user only** |
| Project `.claude/settings.json` | loaded | "Broken symlink or missing file encountered" |
| entire.io hooks | SessionStart + SessionEnd fire ("Entire CLI will link this conversation to your next commit") | **absent — zero references** |
| User-scoped plugins (crit, hookify, agent-skills, security-guidance) | "Registered 7 hooks from 15 plugins" | **identical: "Registered 7 hooks from 15 plugins"** |

**The cause is project-scoped registration in a gitignored file — not cwd alone, and not plugins generally.** entire registers its hooks in the project-scoped `.claude/settings.json`. That file is gitignored (`.gitignore` ends with `.claude`). A `git worktree` is a fresh checkout of *tracked* files only, and git reports the worktree directory as its own `--show-toplevel`, so Claude Code resolves the project root to the worktree and finds no `.claude/` there. User-scoped plugins and MCP servers — registered under `~/.claude/` — are cwd-independent and load identically in both, which is why the symptom looked like "some plugins" rather than "all hooks": only the project-scoped ones drop out. The original hypothesis (worktree cwd) was right that cwd is involved, but the operative mechanism is the gitignored project-settings file, not cwd by itself; were `.claude/settings.json` tracked, the worktree checkout would carry it.

**Reproduction step (one-shot).** From a target repo with project-scoped hooks registered in `.claude/settings.json`:

```bash
# Repo root — interactive-equivalent project root
claude -p --debug-file /tmp/root.debug.log --model claude-sonnet-4-6 \
  --max-budget-usd 0.50 "Reply with exactly: ok"

# Fresh worktree — the worker's actual cwd
git worktree add -b repro .worktrees/repro main
( cd .worktrees/repro && claude -p --debug-file /tmp/worktree.debug.log \
    --model claude-sonnet-4-6 --max-budget-usd 0.50 "Reply with exactly: ok" )
git worktree remove --force .worktrees/repro && git branch -D repro

# Diff the loaded settings/hooks. The worktree run is missing the project
# settings file and any hook registered in it.
grep -iE 'settings.json|Registered .* hooks|<your-plugin-name>' /tmp/root.debug.log
grep -iE 'settings.json|Registered .* hooks|<your-plugin-name>' /tmp/worktree.debug.log
```

The same capture is wired into the worker as an opt-in: `DRAIN_CYCLE_DEBUG=1 drain-cycle` passes `--debug-file` to every spawned session, landing one `<run-log-stem>-<issue>.debug.log` per issue beside the run log in `~/.drain-cycle/runs/`. It is off by default — the diagnostic is for one-shot investigation, not steady-state overhead, and debug output goes to the file rather than stderr so the usage parser's stream is unaffected.

**Decision — symlink a configurable set of project config into each worktree.** The earlier hesitation was upstream of the mechanism: it wasn't obvious headless checkpointing was even *wanted*, since a `drain-cycle` worktree is a throwaway branch. That resolved in favour of wanting it — the checkpoint links a session to the commit it produces, and that commit is pushed to `main` before the worktree is removed, so the link outlives the branch. The operator wants the same project tooling headless as interactively.

After `worktree.add`, the orchestrator calls `worktree.link_project_config`, which symlinks the repo's real project-config entries into the new worktree. Because each link points at the live dir, the worker reads *and writes* the repo's actual config exactly as a non-worktree run does — so a stateful hook like entire's checkpointing works and persists. Teardown needs no special handling: `git worktree remove` deletes the worktree directory and its symlinks but not the link targets, so the repo's real `.claude/` and `.entire/` survive.

This depends on a precondition: every configured path must be gitignored in the target repo. The defaults (`.claude`, `.mcp.json`) and `.entire` are gitignored in a typical repo, so the symlink is invisible to git in the worktree (it shares the repo's tracked `.gitignore`) and the worker's `git add` never stages it. A non-ignored, untracked entry would be the opposite: the worker would stage the symlink into the commit it pushes, and `git worktree remove` would then refuse the dirty worktree. The link step doesn't enforce this — it skips a name only when it's absent in the repo or already present in the worktree — so the requirement is documented (`repos.yml` comment, README) rather than coded. The link step is also not transactional: if `os.symlink` fails partway through the set, the already-created links remain and the failure surfaces as a pre-spawn "setup failed" halt, leaving the worktree in place for inspection.

The linked set is configurable. It defaults to `[.claude, .mcp.json]` — sensible for any repo — and is overridden by an optional `worktree_config_paths` list in `repos.yml`. `.entire` is not a default, because not every repo uses entire.io; an operator who does adds it there. Entries must be relative paths without `..`, since they are resolved inside the repo and linked into the worktree.

Symlink beat the alternatives. Passing `--settings <repo>/.claude/settings.json` loads only one file and leaves four other surfaces broken: `settings.local.json`, project agents/skills/commands, hook scripts whose paths are relative to `$CLAUDE_PROJECT_DIR`, and `.mcp.json`. Copying the config (rather than linking) isolates the worker but discards entire's checkpoint writes when the worktree is removed. Tracking `.claude/` in git would commit machine-specific config. The accepted tradeoff: a worker runs `--dangerously-skip-permissions`, so it shares — and could mutate — the live config dirs, exactly as a non-worktree run would (ADR 0006).
