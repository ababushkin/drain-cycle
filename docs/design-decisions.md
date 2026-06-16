# Design decisions

Design rationale for `drain-cycle`. Read this before making architectural changes — `AGENTS.md` points here.

These decisions serve the project's guiding vision, [`docs/vision.md`](vision.md), and realize the architecture that serves it, [`docs/architecture.html`](architecture.html) — the two-layer supervisor/workflow split on an artifact boundary. Each decision below should hold the vision as its frame; a decision that no longer fits it is the signal to revisit the vision deliberately, not to drift from it silently.

Twenty-two decisions are recorded so a future reader doesn't have to reverse-engineer them from the code. ADRs would be heavier than this tool needs.

## 1. The spawned agent updates Linear itself

> **Superseded-by (pending).** The "Multi-agent collaboration for correctness" Layer-1 project replaces self-asserted Done with a **verifier-gated Done** contract: a ticket reaches Done only with a recorded `outcome_verdict` (its KR2). The supervisor records the verdict; the agent no longer asserts success unobserved. Until that lands, the decision below stands.

The orchestrator does **not** poll Linear and write status. The spawned `claude -p` session is told, in its prompt, to move its issue to Done on completion. The orchestrator only reads Linear after the session exits, to decide whether to advance or halt.

**Alternative considered.** Orchestrator-owned status: the parent polls Linear, transitions states, owns the lifecycle. This is more conventional and easier to reason about.

**Why the agent-self-update path.** The orchestrator can only observe *process exit*, not *task success*. A Claude session may exit 0 having done nothing useful, or exit non-zero having actually shipped — exit code is a poor proxy for "the issue is Done." Letting the agent assert Done in Linear forces it to make an explicit, observable claim about its own outcome, which is exactly the artefact we need to grade KR1 and trigger the kill condition. If this pattern proves unreliable, that's the initiative's kill condition firing — not a bug to paper over.

## 2. `--dangerously-skip-permissions` is accepted

Every spawned session runs with `--dangerously-skip-permissions`. The agent can run any tool on any path inside its worktree, including shell commands, file writes, and network calls, with no operator approval.

**Blast radius.** Bounded to the per-issue worktree under `.worktrees/<issue-identifier>/` inside the target repo, plus the operator's Linear account (the agent can transition issues), plus whatever the spawned shell can reach (env vars, secrets in `~/.config`, network egress). Not bounded to the worktree at the filesystem level — a determined or confused agent can `cd` out.

**Why accepted.** The single-operator personal-product context: target repos are mine, the Linear workspace is mine, the machine is mine. The point of the tool is removing prompts; gating them defeats the purpose. The mitigation is **scope discipline at cycle planning** — don't drain a cycle whose issues touch credentials, production systems, or shared infrastructure. This is operator responsibility, not a tool guarantee.

## 3. Fresh worktree per issue, not a shared workspace

Each issue gets `.worktrees/<issue-identifier>/` branched off `main`, used once, then either removed (on Done) or preserved (on halt).

**Alternative considered.** Shared workspace where all issues run in the target repo's main checkout in sequence. Simpler, faster, no worktree plumbing.

**Why worktree-per-issue.** Issues drift. An agent that misunderstands its task can leave the workspace in a broken state — half-applied edits, uncommitted files, branch in the wrong place — that contaminates every subsequent issue's starting point. The worktree gives each issue a clean, identical starting point regardless of what the previous one did, and preservation-on-halt (US-B) means inspectable debug state. The cost is filesystem space and a few seconds of branch setup per issue. Cheap.

## 4. Run-log is one file per invocation, not one file per cycle

Each `drain-cycle` invocation writes its own run-log file at `~/.drain-cycle/runs/<cycle-id>-<run-timestamp>.json`. The per-file schema is unchanged — `{cycle_id, cycle_duration_seconds, entries: [...]}` — and `cycle_id` inside each file is how downstream readers group runs of the same cycle.

**Alternatives considered.** (A) Multi-run schema in one file: `{cycle_id, runs: [...]}`. Faithful, but every reader has to learn the new shape and the on-disk backup file needs migrating. (B) Open-and-extend: load the existing file and append to a single flat `entries` list. Loses run boundaries, and `cycle_duration_seconds` (computed as `max(finished_at) - min(started_at)`) spans the inter-run gap and becomes misleading. (D) Refuse to clobber: fail-fast if the file exists. Breaks the unattended re-run flow the tool exists for (fix X, re-run, drain the rest) until a resume mode is built.

**Why per-run files.** The bug being fixed (ABA-230) is that a second invocation against the same cycle silently overwrites the first run's data. Per-run files make the write path single-writer-write-once — no read-then-write race, no schema diff, no migration. US-D (ABA-197), which already plans to glob `runs/*.json` and merge across cycles, gets a one-line addition (group by `cycle_id`) instead of a new shape to read. Each file's `cycle_duration_seconds` represents one invocation's hands-off time; US-D sums them when reporting cycle-level KR2.

**Cost.** A re-run cycle accumulates one file per invocation. At cycle scale (≤ 15 issues, rarely more than 2–3 invocations to drain) this is negligible. No retention policy is shipped; trim by hand if it ever matters.

## 5. Each issue declares its target repo via a `repo:<name>` Linear label

The orchestrator used to be single-repo by construction: `repo = Path.cwd()`, every worktree under `<cwd>/.worktrees/`. Cycles in this workspace span multiple repos by design — `linear-workflow.md` makes "Affected repos" part of the six initiative-readiness fields, and the Ops slot deliberately holds cross-repo maintenance issues. Each issue now carries a `repo:<name>` Linear label; `~/.drain-cycle/repos.yml` maps the name to an absolute path; the operator runs `drain-cycle` from anywhere.

**Grouped labels are supported.** The Linear workspace uses label groups (`repo`, `model`, `wave`, …). A child label in the `repo` group — e.g. the leaf `drain-cycle` under the `repo` group — is indistinguishable from a flat `repo:drain-cycle` label to `repos.resolve`. The `pending_issues` query fetches `labels { nodes { name parent { name } } }` and renders each grouped node as `"<group>:<name>"` via `_label_name`; ungrouped nodes keep their bare name (backward-compatible with any literal `repo:<name>` labels already in use). The same rendering applies to `model` group children for `model.resolve` (§7).

**Alternatives considered.**

- *Description-body encoding* (e.g. a `Repo: drain-cycle` line in the markdown). The description is the most-edited surface, agents rewrite it routinely, and there's no schema enforcement. A label is one structured field with one value — Linear validates it, and a label rename triggers a clear "this label doesn't exist" error rather than silently drifting to a wrong repo.
- *Title prefix* (e.g. `[drain-cycle] Fix the foo`). Cleaner machine parse than the body, but every issue's title gains visual clutter for a parser-only concern. Labels don't pay that cost.
- *Inherit from the Linear project's "affected repos"*. Ambiguous for projects that legitimately touch multiple repos, and there is no obvious answer for the Ops container project, which is multi-repo by definition.

**Missing-repo halt behaviour.** Same machinery as every other pre-spawn halt: write a run-log entry, print the `Halt:` line to stderr, exit 1, leave subsequent issues untouched. The new wrinkle is that resolution failures happen before any Linear state is moved, so they skip the post-spawn revert path (`_revert_to_pre_halt_state`). This is the difference from the existing setup-failure halt: that one happens after `worktree.add` or the initial `linear.set_state` attempt; the resolution halt happens before either. Either way, no revert is needed because no state was moved.

**`repos.yml` config errors are even earlier.** A missing or malformed `repos.yml` halts the CLI at startup, before any Linear traffic and before the run-log file is created. There is no cycle yet to log against, so the failure surfaces only on stderr. This is enforced eagerly in `cli.main` so the orchestrator never sees a broken config.

**Why labels over file conventions.** (e.g. requiring the operator to put the issue identifier in a branch comment, or use a remote-name convention). Labels are the only signal that's enforceable in Linear's UI: cycle planners can see at a glance which repo an issue targets, and the multi-repo distinction is visible at the right surface (Linear) rather than buried in a config file.

**Out-of-v1 deliberately.** No env-var expansion inside `repos.yml`; no auto-clone if the path is missing; no parallelism across repos; no retroactive labelling of pre-cycle issues. All are operator-time concerns rather than tool-time concerns. Multi-team Linear support stays out of scope too — the tool is still hardcoded to the `Personal` team.

## 6. Installed as a `uv tool`, with the secret read from `~/.drain-cycle/.env`

`drain-cycle` is installed via `uv tool install`, which puts the executable on `$PATH` in an isolated environment. The Linear API key is read from `~/.drain-cycle/.env` (shell-exported vars still win), beside the `repos.yml` config and the `runs/` logs the tool already kept there.

**Alternatives considered.**

- *`pipx`*. Functionally equivalent for installing a Python CLI in isolation. Rejected because `uv` already anchors this repo's stack (`uv.lock`, the `mise.toml` Python pin) — adding `pipx` spends an innovation token on a second tool that does the same job.
- *Publish to PyPI*. Lets anyone `uv tool install drain-cycle` by name, but buys a release-and-versioning burden — tagging, changelogs, a name on the index — that a single-operator tool doesn't earn. `uv tool install git+https://…` already covers install-from-anywhere with no release step.

**The secret-loading change this forced.** The CLI used to load `.env` only from the repo root (`Path(__file__).parent.parent`). Once installed, the package lives in the isolated tool env, where that path has no `.env` — so the key has to live somewhere stable. The load order is now shell env → `~/.drain-cycle/.env` → repo-root `.env`, first hit wins (`load_dotenv` defaults to `override=False`). The repo-root entry survives only as a dev-checkout fallback (it works under `--editable`, where `__file__` still points into the checkout); the installed tool reads the key from `~/.drain-cycle/.env`.

**Out-of-v1 deliberately.** No PyPI release. No `drain-cycle init` command to scaffold `~/.drain-cycle/` — a missing `repos.yml` already halts with an actionable message that prints the expected shape, and `docs/repos.example.yml` is a copyable template, which is enough for a single operator.

## 7. Workers default to Sonnet; a `model:` label overrides per issue

A spawned `claude -p` worker inherits whatever model the operator has globally pinned. In the diagnosed quota-burn run all five workers ran on `claude-opus-4-7` (the operator's global pin), and Opus was the single largest cost multiplier of the ~108M-token spend. Workers now default to `claude-sonnet-4-6`, passed explicitly via `--model`; an individual issue opts up (or down) with a `model:<alias>` Linear label, mirroring the `repo:<name>` mechanism. Known aliases (`sonnet`/`opus`/`haiku`) map to full ids; an unrecognised value is passed to `claude --model` verbatim.

**Why lenient, not strict.** Unlike `repo:` resolution — where a missing label is a hard halt because there is no safe default target — model resolution always has a safe fallback. So it never raises: no label, an unknown alias, or conflicting `model:` labels all fall back to the default rather than halting an unattended cycle over a label typo. The model actually used is recorded in the run log, so a mis-labelled issue surfaces after the fact instead of stalling the run.

**Grouped model labels.** A child of the Linear `model` group — e.g. the leaf `sonnet` under `model` — renders as `model:sonnet` via the same `_label_name` projection described in §5. `model.resolve` sees it identically to a flat `model:sonnet` label.

**Alternatives considered.** (A) Keep inheriting the global pin — rejected, it is exactly what caused the burn and gives the operator no per-issue control. (B) A single global `--model` flag with no per-issue override — simpler, but a cycle legitimately mixes cheap mechanical issues with a few that warrant Opus; per-issue is the right grain. (C) Raise on ambiguous labels like `repo:` does — rejected, halting a whole unattended cycle over a duplicate label is worse than silently taking the cheap, safe default.

## 8. Workers use stream-json output; usage is parsed from the wire and the worker leads its own process group

The worker launches with `claude -p --verbose --output-format stream-json` via `subprocess.Popen(..., stdout=PIPE, stderr=STDOUT, text=True, bufsize=1, start_new_session=True)`, reading output line by line in a reader thread. The per-issue run-log entry gains `model`, a `usage` block (the four token components + `cumulative` + `peak_context`), `cost_usd`, `num_turns`, `session_id`, `is_error`, and an explicit `duration_seconds`; the file gains top-level `cycle_cost_usd` and `cycle_tokens_cumulative`. All of this lives in `drain_cycle/worker.py`; the orchestrator calls `worker.run_issue(...)` and records the result.

**The problem.** A run that burned ~108M tokens left the operator with no on-disk record of *which issue* spent what — usage had to be reconstructed from `~/.claude/projects/*.jsonl`. Spend is the metric the cost guardrail (a downstream slice) acts on, so it has to be captured at the source.

**Why parse the stream rather than the JSONL transcript.** The transcript files are keyed by session and live outside the worktree; correlating them back to an issue after the fact is exactly the manual step this removes. The event stream is emitted by the process we already spawn, in real time, and carries everything we need.

**Token accounting — dedup by `message.id`.** The same `assistant` message is emitted *once per content block* (thinking, text, tool_use), each copy repeating the identical `message.usage`. Summing per event double-counts a turn, so usage is keyed by `message.id` and counted once. `cumulative` sums all four token components across turns — the real billed total, dominated in long tool-use loops by cache reads re-paid every turn (this is what the 108M figure was). `result.usage` is deliberately *not* used for the totals: it is only the final turn's snapshot, and it is absent entirely when a session is killed before finishing. The terminal `result` event is authoritative only for `cost_usd` (`total_cost_usd`), `num_turns`, `session_id`, `is_error`; those are `null` on a killed session.

**Why `start_new_session=True` + `os.killpg`.** The old `subprocess.run(timeout=)` killed only the direct child on timeout, orphaning grandchildren — MCP servers, sub-agents — that kept consuming. Making the worker a process-group leader and SIGKILLing the whole group on the time cap reaps them. SIGKILL with no SIGTERM grace is deliberate: a session past its deadline has no clean-shutdown work worth waiting for, and SIGKILL is the only signal a wedged grandchild cannot ignore. Killing the group also closes the stdout pipe those grandchildren inherited, which is what lets the reader thread reach EOF instead of blocking.

**Additive schema.** `grade.py` reads only `cycle_id` and `entries[].final_linear_state` / `exit_code`, so the new fields don't touch grading and pre-existing run logs grade unchanged. Entries written before any session runs (resolution and setup-failure halts) carry `null` for the worker fields but keep the same key set.

- **Parallelism.** Issues run one at a time. The Linear cycle is the unit; intra-cycle parallelism adds resource contention and serialises poorly with the agent-self-update pattern (two agents racing to mark different issues Done is fine, but two agents racing on overlapping files is not).
- **Retry.** Superseded by §14 — a halted issue is now resumed automatically on re-run by reusing its preserved worktree, bounded by ``max_resume_attempts``. The operator-owned manual path (inspect, fix, redo, descope) still applies; the change is that the default `drain-cycle` re-run no longer fails on a leftover worktree.
- **Cross-cycle scheduling.** One cycle per invocation. Chaining cycles is an operator concern.

## 9. Resource guardrails: a native cost belt and orchestrator token/time suspenders

Before this, the only ceiling on a worker was a 3600s wall-clock timeout, and the cycle as a whole had none. A single diagnosed run burned ~108M tokens across five issues with no circuit-breaker. `drain_cycle/limits.py` now defines per-issue and cycle-wide caps on tokens, wall-clock, and cost, enforced in two layers:

- **Native belt.** The per-issue cost cap is passed to `claude` as `--max-budget-usd`, so the session self-terminates on spend without the orchestrator watching it.
- **Orchestrator suspenders.** The per-issue token and time caps are enforced by the worker against the live event stream: a poll loop compares the running cumulative-token tally and elapsed wall-clock against the caps and SIGKILLs the session's process group on the first breach (reusing the group-kill machinery from decision 8). The cycle-wide caps are enforced by the orchestrator between issues — after each Done issue it sums the run log's running totals and stops the run if any cycle cap is crossed.

**Why both layers.** The cost belt is the cheapest possible enforcement — `claude` already meters its own spend — but it only knows about *this* session's dollars, and a subscription user cares about tokens, not dollars. The token cap is therefore the primary guardrail and has to be the orchestrator's job, since `claude` exposes no `--max-tokens` equivalent for a whole session. Time is enforced the same way because a session can wedge while emitting no usage at all (the old 3600s timeout's job), so wall-clock can't be inferred from the token stream.

**Why the cycle caps live in the orchestrator, not the worker.** A worker only sees its own issue. The failure mode the cycle caps exist for is death-by-aggregate: every issue stays comfortably under its per-issue cap while their sum drains the quota (8M × 5 = 40M, past a 30M cycle cap, with no single issue ever breaching). Only the orchestrator, which holds the run log's running totals, can see that — so it checks after each Done issue and stops before spawning the next.

**Defaults: per-issue 8M tokens · 20 min · $15; cycle 30M tokens · 90 min · $60.** These are deliberately generous starting points sized below the diagnosed bad run (one issue alone hit 43M tokens / 23 min), not tuned values. The intent is a circuit-breaker that trips on the pathological case, not a tight budget. They are meant to be recalibrated against real run-log spend.

**Each guardrail is independently on/off-able.** Any cap can be `None` (off). The defaults are all live; an operator turns one off with `null` in the optional `~/.drain-cycle/limits.yml`. With both the per-issue token and time caps off, the worker simply waits for the session to exit on its own — there is no longer any implicit outer timeout, which is the operator's explicit choice when they disable both.

**The time cap absorbed the old 3600s timeout.** Rather than keep a separate hardcoded outer timeout alongside the configurable time cap, the time cap *is* the timeout — `per_issue_seconds` (default 20 min, tighter than the old 3600s and below the diagnosed 23-min overrun). One time concept, configurable, instead of two.

**Breach reporting.** A breach is a small `Breach(scope, metric, limit, observed)` value whose `describe()` renders the operator-facing line — used verbatim by the worker (per-issue kill) and the orchestrator (cycle stop) so the wording can't drift. A per-issue breach takes the existing exit-1 + revert + `halt_reason` contract, naming the cap and the value at kill time. A cycle breach lands in the run log's top-level `cycle_halt_reason`: the breaching issue's own entry is a normal Done, and the top-level field explains why the run stopped.

**`limits.yml` semantics and validation.** Absent key → baked-in default; explicit `null` → guardrail off; positive number → override. A present-but-malformed file (unknown key, non-positive, non-numeric, bool, invalid YAML) raises `LimitsConfigError` and halts at CLI startup — mirroring the eager `repos.yml` validation (decision 5). The reasoning is sharper here: silently falling back to defaults on a typo would leave the operator believing a tighter cap was active when it wasn't, which is worse than a loud halt.

**Alternatives considered.**

- *CLI flags to override limits per invocation.* Deferred. The acceptance criteria require only defaults + `limits.yml`, and `cli.main` is a deliberately minimal exact-match dispatcher (decision in `cli.py`); adding an argument parser to thread per-run overrides is scope the single operator can cover by editing `limits.yml`. Revisit if a use case for one-off overrides appears.
- *Kill on the cycle cap mid-stream (pass cycle-so-far totals into the worker).* Rejected as unnecessary: the per-issue cap (8M) is below the cycle cap (30M), so a single issue can't cross the cycle cap before crossing its own; checking between issues catches the aggregate case without coupling the worker to cycle state.
- *A separate hardcoded outer timeout kept alongside the configurable caps.* Rejected — two time concepts where one suffices (see above).

## 10. Headless workers inherit project-scoped config by symlink

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

Symlink beat the alternatives. Passing `--settings <repo>/.claude/settings.json` loads only one file and leaves four other surfaces broken: `settings.local.json`, project agents/skills/commands, hook scripts whose paths are relative to `$CLAUDE_PROJECT_DIR`, and `.mcp.json`. Copying the config (rather than linking) isolates the worker but discards entire's checkpoint writes when the worktree is removed. Tracking `.claude/` in git would commit machine-specific config. The accepted tradeoff: a worker runs `--dangerously-skip-permissions`, so it shares — and could mutate — the live config dirs, exactly as a non-worktree run would (decision 2).

## 11. Active-run marker lives above `runs/` as `~/.drain-cycle/active.json`

Without a live-run signal there is no way to distinguish a working run from a hung one: the run-log gains an entry only on issue completion, and the orchestrator emits only sparse stderr lines. The fix is an active-run marker — a small JSON file written before each spawn and removed in the worker's try/finally — that a second terminal can read with `drain-cycle status`.

**Why `~/.drain-cycle/active.json`, not inside `runs/`.** The `grade` command globs `runs/*.json` and groups files by `cycle_id`. A marker placed in `runs/` would either corrupt a grading run (if it looks like a run log) or require `grade` to skip it by sentinel field (fragile coupling). Placing the marker at `~/.drain-cycle/active.json` — one level above `runs/` — means `grade`'s glob never sees it and the two concerns share no code path.

**Why not `runs/active.json`.** Same issue: inside `runs/` it's in the glob's scope. A separate directory (`~/.drain-cycle/live/`) was considered but adds a layer without benefit; a single well-named file at the parent level is enough.

**Why atomic write (temp-file rename).** `drain-cycle status` reads the marker from a different process, potentially mid-write. `Path.write_text` is not atomic: the file is truncated before the new content is written, so a reader arriving between those two steps sees an empty file. Rename is atomic on POSIX filesystems: the reader sees either the old complete content or the new complete content, never a partial write. The temp file uses a `.tmp` extension adjacent to the marker (`active.json.tmp → active.json`), not a different directory, so the rename is always within the same filesystem mount.

**Why the progress block is updated on every new turn, not on every raw JSON line.** The worker's stream-json output emits one event per content block per turn (thinking, text, tool_use) — a single turn with a thinking + tool_use block produces two events carrying the identical usage. Firing the callback on every raw event would write the file multiple times per turn with the same data, wasting I/O and producing redundant stderr lines. The reader thread deduplicates by message id: the callback fires once per unique message id, which is once per turn. The first event in a turn records the turn; subsequent events with the same id are no-ops for the callback.

**Stale marker detection.** A crash or SIGKILL leaves the marker on disk (the try/finally doesn't run on SIGKILL). `drain-cycle status` checks `os.kill(pid, 0)` — if the pid is gone it reports a stale marker rather than a live run. It does not delete the marker automatically; the operator removes it with `rm`, preserving forensic evidence of the interrupted run.

## 12. Execution order: manual drag-order only, blocks-aware

Before this change the orchestrator ordered issues by `(priority, sortOrder)` and ignored blocks/blocked-by entirely. Priority overrode the operator's manual drag-order, and a blocked issue could run before its blocker — wasting a worker on work that couldn't succeed.

**Decision.** Order purely by manual `sortOrder` ascending; drop `priority` from sorting. Overlay a topological pass (Kahn's algorithm) using `(sortOrder, id)` as the tiebreak among ready issues: each issue is "ready" once all its intra-drain blockers have been scheduled. This preserves the operator's intended drag-order as far as dependencies allow.

**External unresolved blocker → defer.** If a pending issue is blocked by an issue not in this drain's runnable set — including an In Progress issue in the same cycle, which the drain won't complete — the blocked issue is deferred: left Todo, not spawned, logged to stderr. Deferral cascades: if X is deferred and X intra-drain-blocks Y, Y defers too (falls out naturally from Kahn, since Y's in-degree never reaches zero while X is treated as permanently deferred). The exclusion rule is "defer unless the blocker is `completed` or `canceled`" — so `started`/`unstarted`/`backlog`/`triage` external blockers all defer. Deferred issues are not run-logged: they were never attempted, and counting them in `done/attempted` would corrupt `grade`'s completion signal.

**Intra-drain cycle → halt.** If the blocks graph among the pending issues contains a cycle (self-loop included), `DependencyCycleError` is raised, the orchestrator sets `cycle_halt_reason`, prints a `Halt:` line naming the involved issues, and exits 1. Nothing runs.

**`pending_issues` return type changed from `list[dict]` to `ExecutionPlan`.** The pure function `_plan(issues) -> ExecutionPlan` replaces `_sort_pending_issues`. `ExecutionPlan` is a frozen dataclass carrying `order: list[dict]` (runnable issues, topo-sorted) and `deferred: list[dict]` (each entry has `issue`, `blocker_identifier`, `blocker_state_type`). `DependencyCycleError(RuntimeError)` carries `identifiers: list[str]` for the cycle report. The `inverseRelations` GraphQL field is fetched in `pending_issues`, flattened to `issue["blockers"] = [{id, identifier, state_type}]`, and the raw key dropped — wire shape stays local to `pending_issues`, like `labels`.

**All-deferred → exit 0.** If `plan.order` is empty but `plan.deferred` is not, the run emits the stderr deferral lines and returns 0: the cycle isn't broken, it's just blocked externally. An empty `plan.order` with an empty `plan.deferred` is also exit 0 ("nothing to do").

**Alternatives considered.**

- *Keep priority in the sort key.* Rejected: the operator has one predictable knob — the manual drag-order — and priority overriding it is surprising and undesirable.
- *Ignore blocks entirely.* Rejected: running a blocked issue wastes a worker on work that can't succeed by definition.
- *Best-effort run instead of defer/halt.* Rejected: silently violating a dependency the operator encoded is worse than a loud skip or stop.
- *Record deferrals in the run log.* Rejected: a deferred issue was never attempted; counting it as an entry corrupts `done/attempted` in `grade`.

## 13. Opt-in OpenTelemetry tracing to Honeycomb

A drain runs unattended and can take hours, spending real tokens across many spawned sessions. The run log records the outcome per issue, but it is one flat file per invocation — it can't show where time went inside a drain, how the Linear round-trips and worker sessions nest, or let an operator aggregate cost across drains. Tracing fills that gap.

**Decision.** Each invocation emits one trace: a `drain.cycle` root span, a `drain.issue` span per attempted issue, and under those the `drain.worker.session`, `drain.worktree.add`/`.remove`, and per-operation `linear.*` spans. The `httpx` transport is auto-instrumented, so every Linear GraphQL POST appears as a child of its `linear.*` span. Worker token/cost/turn usage, the issue's repo/model/final-state, and the cycle outcome ride as span attributes; every halt site is tagged with a static `exception.slug` (greppable, low-cardinality, safe to `GROUP BY`). `service.name` is `drain-cycle`, which is also the Honeycomb dataset.

**Opt-in via `HONEYCOMB_API_KEY`.** The key's presence in the environment is the on/off switch. Absent, `telemetry.setup()` is a no-op and the default no-op tracer stays installed — a drain with no telemetry configured behaves exactly as before, takes no new network dependency at runtime, and the `start_as_current_span` calls scattered through the code cost nothing. The OTel packages are unconditional install-time dependencies (lightweight, pure-Python); only *exporting* is gated.

**Flush-on-exit is load-bearing.** `drain-cycle` is a short-lived CLI that exits through `sys.exit`. A `BatchSpanProcessor` buffers spans and exports on a timer, so without an explicit flush the queued spans die with the interpreter and the last issues of a drain never ship. `setup()` registers `shutdown()` (which flushes the processor) with `atexit`; `SystemExit` still runs `atexit` handlers, so every exit path drains the queue.

**Alternatives considered.**

- *`opentelemetry-instrument` zero-code agent.* Rejected: it wraps a `python` invocation, but the tool ships as a `uv tool` console-script entry point (`drain-cycle`), so there is no `python app.py` to wrap. Programmatic setup in `telemetry.setup()` is reliable regardless of how the entry point is launched.
- *Always-on tracing.* Rejected: it would force an exporter and an egress dependency on an operator who hasn't asked for it, and fail noisily (or silently retry) when Honeycomb is unreachable. Opt-in keeps the default path dependency-free.
- *Metrics and logs alongside traces.* Out of scope. The run log already covers durable per-issue accounting; traces add the causal/nesting view. A metrics layer can be added later if cost-rate alerting is wanted (see the otel-instrumentation layering guidance).
- *A span per private helper (e.g. `_plan`, `link_project_config`).* Rejected as over-instrumentation: those are fast, pure, and not independently aggregable. Interesting, failure-prone, or aggregable operations get spans; the rest stay as attributes on their parent.

## 14. Halted issues resume on re-run by reusing the preserved worktree

Before this, a halted issue's worktree was preserved on disk for the operator to inspect, but a re-run of `drain-cycle` against the same cycle would call `git worktree add` on the same path and fail opaquely — every halt required a manual `rm -rf .worktrees/<id>` plus `git worktree prune` plus `git branch -D <id>` before the next run could even reach the spawn. That friction punished the exact workflow the tool exists for: "halt, inspect, re-run, drain the rest."

**Decision.** `worktree.ensure` replaces `worktree.add` in the orchestrator's pre-spawn path: if a worktree is already registered at `repo/.worktrees/<identifier>`, it is reused as-is (no mutating git command runs, so a dirty index, staged or untracked files, and the gitignored config symlinks all survive untouched); otherwise it falls through to `add` exactly as before. The handle returned carries a `resumed: bool` flag that the orchestrator threads into `prompt.build(..., resumed=…)`. When true, the spawned prompt prepends a "Resuming issue …" directive that tells the agent to run `git log --oneline main..HEAD` and `git status` first to read what is already done before continuing — so the agent does not restart from scratch and clobber the prior work.

**Bounded by `limits.max_resume_attempts`.** A perma-stuck issue would otherwise consume an attempt on every re-run forever. The orchestrator counts prior halted attempts for the issue across the cycle's run-log files (entries with a non-Done `final_linear_state` matching the identifier) and refuses to spawn once that count *exceeds* the cap. The refusal is a no-spawn halt: no Linear `set_state`, no worktree manipulation, no worker invocation; a halt entry is still written so KR1 grading sees the refused attempt and the halt line names the cap so the operator knows how to clear it (raise the cap, clear prior runs, or finish by hand). The semantic follows the stdlib `urllib3` / `requests` convention for `max_retries`: `max_resume_attempts=N` allows up to N resumes *after* the initial attempt, for `N+1` total halts before refusal. The default is 3 (one fresh attempt + three resumes = four total halts before the fifth would be refused); `null` removes the cap entirely. `max_resume_attempts` is a *policy* cap, not a runtime guardrail in the §9 sense — no `Breach` is raised, the check is purely pre-spawn against the run-log history, and the field validation is integer-only (`1.5` would be incoherent on a count).

**Why the cap-halt fires before `worktree.ensure` and `set_state`.** The point of refusing is to leave the issue exactly where it was so the operator can intervene without untangling a half-done re-run. Calling `worktree.ensure` first would be harmless (`ensure` is read-only on the worktree-already-registered branch), but `set_state(In Progress)` would not — a refused attempt that nevertheless flipped Linear to In Progress would silently exclude the issue from the next `pending_issues` query and the cycle would stall invisibly. The order is: resolve repo → resume-cap check → `worktree.ensure` → `link_project_config` → `set_state` → spawn. Each step is a pure no-op until the one before it succeeds.

**Why "resumed" is a prompt directive, not a worker flag.** The worker is just a `claude -p` subprocess; the only contract surface between orchestrator and agent is the prompt string. Adding a CLI flag for "resumed" to the worker would still resolve to "include this paragraph in the system context," and the prompt is already that context. The four-segment ordering in `prompt.py` (title → body → preamble → tail) is load-bearing; the resume directive inserts as the first line of the preamble (after the `---` separator, before "Execution instructions:") so the agent reads it ahead of the procedure while `_TAIL` keeps the last-line position the ordering reserves for it.

**Run-log entries treat resume halts the same as first-attempt halts.** A halt entry written on a resumed run has identical shape to one from a fresh attempt — same `final_linear_state`, same `worktree_path`, same `halt_reason` template via `_halt_message`. This is deliberate: KR1 grading and the cap-counting helper both read `final_linear_state` per entry, and giving resumed halts a different shape would split the schema into two cases for no benefit. The cap-halt entry uses `exit_code=-1` (the no-spawn sentinel, like the resolution and setup-failure halts in §8/§5) and carries the cap-specific message in `halt_reason` so the operator can grep for it.

**This supersedes the `rerun-after-halt-detect-cleanly` plan (`docs/tasks/`).** That earlier doc proposed a `PriorArtefactsExist` exception that would convert the leftover worktree into a clean `Halt:` line — same friction, prettier error. The resume path eliminates the friction entirely: the operator's mental model is "re-run drains the rest," and `drain-cycle` now matches it. The task doc carries a supersession note pointing here.

**Alternatives considered.**

- *Auto-delete the preserved worktree on re-run.* Rejected. The worktree exists because §3 chose preservation-on-halt for inspectability (US-B). Deleting it on re-run means the operator's evidence is gone the moment they re-run, which is the worst-of-both: they cannot inspect (deleted) but also cannot resume (fresh worktree, no prior work). Preservation + resume is the only combination that lets the operator both inspect *and* re-run.
- *Refuse to re-run while a halted worktree exists, force `drain-cycle clean <id>` first.* Rejected as scope creep — a new subcommand plus a new gating rule, both to enforce the workflow `drain-cycle` already trains. The mental model "halt, inspect, re-run" is the one operators have; a forced clean step adds friction without adding safety (the operator could already have re-run after deletion in the old model).
- *Resume by replaying the agent's last few turns from the worker stream-json log.* Rejected. The transcript is keyed by session and lives outside the worktree (§8); reconstructing context from it duplicates what `git log main..HEAD` and `git status` already tell the agent at the start of a resume. Two sources of truth where one suffices.
- *Resume by passing `--continue` or `--resume <session-id>` to `claude -p` instead of changing the prompt.* Rejected because a halted worker may have died mid-turn with no clean session boundary; the next worker is a fresh `claude -p` against a worktree that already has commits. The prompt directive is the right level of abstraction: "this worktree carries prior committed work; read it before continuing." The mechanism is the same whether the prior session lived for one turn or one hour.
- *Cap-halt resets when the operator manually marks the issue Done in Linear.* Considered — the cap counts non-Done entries, so a manual Done in Linear does NOT directly reset the cap, because the prior halted entries in the run log still carry their non-Done `final_linear_state`. The operator clears the cap by raising it, deleting the relevant run-log files, or simply not re-running. This is fine: the cap exists to prevent infinite resume loops, not to track Linear state — Linear state can flip independently of the on-disk history.

## 15. `--watch` runs claude *in* the tmux pane, not a formatter tailing a log

In `--watch` mode the operator wants to see the agent working in real time. The pane therefore **is** the `claude` session: the orchestrator runs `claude … | tee <fifo>` in a `tmux split-window`, and the same stream-json bytes that scroll in the pane flow through a named pipe (FIFO) to drain-cycle's reader thread. The reader opens the FIFO instead of a `subprocess.PIPE`; usage accounting, cost, and breach detection are byte-for-byte identical to the spawned path — there is exactly one `claude` process, and both consumers (the operator's terminal and the parser) see its raw output.

**What this replaces.** The first cut tailed a *secondhand* view: the worker spawned `claude` normally, an internal `_WatchWriter` formatted each event into a human-readable activity log, and the pane ran `tail -f` on that file. The operator saw a filtered summary produced by drain-cycle, not the live session — and the formatter was a second place for stream-json knowledge to rot. `tee`-into-a-FIFO deletes the formatter and the intermediate file entirely (`runlog.watch_path` and `_WatchWriter` are gone): the pane shows precisely what `claude` emits.

**FIFO startup is non-blocking with a timeout.** drain-cycle opens the read end `O_RDONLY | O_NONBLOCK` and `select`s up to ten seconds for the pane's `tee` to produce its first bytes, then clears `O_NONBLOCK` so the reader thread blocks normally until EOF. A reader-only FIFO with no writer is *not* readable in `select` until a writer connects (verified on macOS/BSD and Linux), so a pane that never started can't wedge the drain — the `select` simply times out. On timeout (or any pane/FIFO setup failure, or no `$TMUX`), the pane is torn down and the issue runs through the **normal subprocess path** unchanged. Watch is a convenience overlay; it never gates whether the cycle drains.

**Breach kills the pane, not a process group.** On the spawned path a per-issue breach SIGKILLs the worker's process group (§9). On the watch path there is no child process to signal — `claude` belongs to the pane — so the worker calls a `kill_fn` the orchestrator wires to `tmux kill-pane`. Killing the pane terminates `claude` and its `tee`, which closes the FIFO write end and lets the reader reach EOF. The pane command ends in `; exec ${SHELL}` so the pane survives `claude`'s *normal* exit (the final issue's scrollback is preserved, AC of the pane-lifecycle: prior pane killed when the next issue starts, last one left open); `tee` still closes the FIFO on claude's exit, so EOF fires regardless of the trailing shell.

**`exit_code` is `0` on the watch path.** The pane owns `claude`, so its real return code isn't observable from drain-cycle. The run-log entry records `exit_code=0`; the breach field and the result event's `is_error` carry the real outcome, and KR1 grading keys on `final_linear_state`, not the exit code. This is the one fidelity gap versus the spawned path, and it is cosmetic.

## 16. The Graphite PR-stacking sequence the orchestrator will run (ABA-300 spike)

> **Superseded by §19.** The orchestrator no longer assembles or submits the stack. The `gt`/`gh` sequence below is now run by the worker's finishing skill, not the orchestrator; the verified commands stay accurate, but the actor is the skill. See §19.

Decided empirically by ABA-300: the full `gt` + `gh` stacking sequence was driven by hand against this repo with two stacked branches (`spike/aba-300-step-a` off `main`, `spike/aba-300-step-b` off A), producing PRs [#5](https://github.com/ababushkin/drain-cycle/pull/5) and [#6](https://github.com/ababushkin/drain-cycle/pull/6). The four findings below are what the orchestrator (Ticket 2) wires against — verified commands, not guesses.

**(a) `gt` works from a linked worktree; cwd = the per-issue worktree root.** `gt track` and `gt submit` were run from inside `.worktrees/spike-a` and `.worktrees/spike-b` (linked worktrees created with `git worktree add`), and both succeeded. The Graphite metadata DB lives in the shared `.git` dir (`.git/.graphite_metadata.db`), so every linked worktree sees the same stack state — `gt ls` from worktree B even annotates which worktree each branch is checked out in. **The orchestrator runs `gt` from the per-issue worktree root** (§3's "fresh worktree per issue"), the same cwd the worker already uses. No need to run from the primary checkout.

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

## 17. `/shape:task` runs inside the worker session; whole-project shaping stays at design-doc scope

> **Forward-pointer.** Not stale today, but the target moved into the pack. Under the `exec:*` namespace (ADR 0004 / Shaper `execution-workflow` design doc), `/shape:task` folds into `exec:pickup`/`exec:breakdown` at the keystone cutover (§24). The in/out contract below still describes what that step does.

The M2 worker pipeline introduces a Task Shaper role — `/shape:task` — that runs before the implementer on verify-flow tickets. The initiative doc deferred the invocation interface to this ADR: whether whole-project shaping (via `/shape:planning-and-task-breakdown`) pre-computes per-ticket task lists that workers later read, or `/shape:task` runs inside each worker session on its own ticket.

**Decision: `/shape:task` runs inside the worker session, invoked once per ticket before the implementer begins.** The skill takes a Linear issue identifier, fetches the live issue from Linear via MCP, and produces an enriched AC checklist + sizing decision + per-stack task list. The output is fed to the implementer in-session and written to the Linear issue body. The Linear write is the durable artefact — a worker resuming a halted issue (§14) reads the existing output from the issue body rather than re-invoking the skill.

**Why the per-ticket, in-worker path.** The alternative (pre-computation) would require the operator to run `/shape:planning-and-task-breakdown` before any drain can begin, turning the whole-project skill into a planning gate every drain must pass through. `drain-cycle`'s autonomous-drain promise is a single `drain-cycle` invocation, unattended. Fracturing that into "run planning, then drain" creates ceremony the tool exists to remove. A worker that calls `/shape:task` itself is self-contained: the same invocation path works for a cycle drain, a single-ticket re-run, or a §14 resume — no prior planning artefact required.

**Rejected alternative: `/shape:planning-and-task-breakdown` pre-computes per-ticket task lists.** The whole-project skill runs once before the drain, writes structured per-ticket task lists somewhere (a design doc, a Linear comment, an issue body block), and workers read those artefacts at session start. The appeal is operator review of decomposition before any code runs. The costs are: (a) a mandatory pre-drain gate that breaks the single-invocation promise; (b) a machine-readable output contract that `/shape:planning-and-task-breakdown` must emit and every worker must parse — a shared format across two skills and their update paths; (c) stale-data risk when the issue body changes between planning and execution; (d) the only way to know whether the pre-computed list still applies is to re-derive it, making pre-computation an expensive cache that must be manually invalidated. The two skills stay at distinct scopes: `/shape:planning-and-task-breakdown` operates on a whole design doc; `/shape:task` operates on a single Linear issue.

**`/shape:task`'s input/output contract, as constrained by this decision.**

*Input:* A Linear issue identifier. The skill fetches the current issue body, title, and AC from Linear at invocation time. It does not read any prior planning artefact.

*Output:*
- **Enriched AC checklist** — AC items made concrete, implicit contracts surfaced, irresolvable gaps flagged (the skill notes gaps and continues; it does not hang waiting for operator input).
- **Sizing decision** — `single-stack` (all work on one branch) or `N-stacks` (N specified and justified), based on whether the implementation can reach Done in a single PR stack.
- **Per-stack task list** — one ordered task list per stack, intended as direct context for the implementer.

*Dual-write:* Output is (a) returned to the calling worker for in-session context and (b) appended to the Linear issue body. The Linear write is authoritative: a resumed worker reads the block from the issue body instead of re-running the skill. The skill must emit the output in a stable, delimited format that a future invocation can detect (to avoid silently double-writing).

## 18. PR links are recorded in the run-log and posted to Linear by the orchestrator

> **Superseded by §19.** This decision assumes the orchestrator assembles the stack, so `graphite.submit` returns the URL and the orchestrator posts the Linear comment. Under §19 the worker's finishing skill submits and writes `pr_urls` into `.drain-handoff.json`; the orchestrator *reads them back* rather than producing them. The recording intent survives — memory lives in artefacts — but the actor and the source of the URL changed.

After a stack-mode issue is confirmed Done and its branch is assembled into the per-repo Graphite stack (§16), the orchestrator needs to close the loop: the operator and any future session must be able to find the PR without reading source or re-running `gh`. Memory lives in artefacts.

**Decision.** The orchestrator already holds the PR's URL and number — `graphite.submit` returns them (`gh pr view` runs as the final step of assembly). On a successful submit it:

1. Records `pr_url`, `pr_number`, `review_high` (the flag computed from the Linear label and the handoff findings, the same one that drives the GitHub `review:high` label), and `parent_branch` (the stack parent the branch was tracked under) in the run-log entry alongside the existing usage fields.
2. Posts a comment on the Linear issue via `linear.add_comment` (GraphQL `commentCreate`) with the PR URL and, when flagged, a `review:high` note.

All four run-log fields are additive and default to `null` (push-to-main repos, halted issues, pre-PR run-logs). `grade.py` reads only `cycle_id`, `final_linear_state`, and `exit_code`, so pre-existing run-logs grade unchanged. The Linear comment is non-fatal: any failure is logged to stderr and the drain continues. The PR link is informational, never load-bearing for the drain's control flow.

**Why the submit result, not a post-hoc `gh pr list` lookup.** An earlier draft of this decision had the orchestrator re-query GitHub by head branch after the worker exited — necessary in a design where the *worker* ran `gt submit`. Under §16's orchestrator-assembles design the lookup is redundant: the assembly step that creates the PR returns its URL and number in the same call, with no second query, no race against GitHub's index, and no dependency on branch-name conventions.

**Why the orchestrator, not the agent.** The agent commits without pushing and writes the handoff file; it never talks to GitHub. The orchestrator is also the only component that can write to the run-log — it owns the `RunLog` object and calls `append_entry`.

**Alternatives considered.**

- *Post-hoc `gh pr list --head <branch>` lookup.* Rejected as redundant once assembly moved into the orchestrator — see above.
- *Always post the PR comment unconditionally (even on halt).* Rejected: a halted issue has no submitted PR (a graphite failure halts before this step). The comment fires only on confirmed Done with a successful submit.

## 19. The worker owns PR submission via the finishing skill; the orchestrator reads `pr_urls` back

This reverses the actor in §16 and §18. There, the orchestrator assembled and submitted the per-repo Graphite stack and posted the Linear comment. Now the **worker** does it: in drain mode the worker commits reviewable slices, then runs the finishing skill (`/shape:pr-finishing` today; `exec:finish` after the keystone cutover, §24), which owns submission — it drives `gt`/`gh`, writes the submitted PR URLs into `.drain-handoff.json` (`pr_urls`), and posts the review-summary comment on the Linear issue. The orchestrator no longer assembles or submits anything; it **reads `pr_urls` back** as confirmation that submission succeeded, and a Done stack-mode issue with no `pr_urls` halts the run rather than letting the next issue stack on an unpushed branch.

**Why the reversal.** It is the artifact boundary applied ([`architecture.html`](architecture.html) §5). Submitting a stack is "what a role does" — Layer 2 — so it belongs in a skill that runs identically by hand or unattended. Reading back whether the PRs exist is "whether an artifact exists" — Layer 1 — so it stays in the supervisor. The §16/§18 design put a Layer-2 action inside Layer 1, which is exactly the coupling the two-layer split removes: an orchestrator that knows the `gt`/`gh` sequence cannot be the thin, vendor-agnostic supervisor the keystone (§24) requires.

**The verified `gt`/`gh` sequence in §16 is still correct** — it is just run by the finishing skill, not the orchestrator. §16's per-repo preconditions (`gt auth`, `gt init --trunk main`) and its stop-the-line restack policy carry over unchanged.

**Completion recovery preserves the boundary.** When a worker exits leaving committed slices but the issue is not properly closed (not Done, or Done-without-`pr_urls`), the orchestrator does not run `gt`/`gh` itself — it spawns a fresh finishing sub-agent that runs the skill, then re-checks the contract, and only halts if completion still fails. A worker that left no committed slices halts as untrusted. (Tracked by the "Orchestrator-enforced completion" delivery plan.)

**Alternatives considered.**

- *Keep the orchestrator assembling the stack (§16/§18 as written).* Rejected: it hard-codes the PR-tooling sequence into Layer 1, blocking the keystone and the vendor-agnostic worker.
- *Worker pushes by hand instead of via the skill.* Rejected: the skill is the single place the submission procedure lives, so it stays identical in interactive and drain modes; a hand-rolled push in the worker prompt would be a second, drifting copy.

## 20. A worker per phase, not a worker per issue

Today one spawned worker runs the whole `exec:*` chain for an issue — pickup through finish — in a single session. The decision is to split it: each phase (code, review, finish) is its own spawn, with its own model tier, and the agent that produced an artifact never reviews it. See [`architecture.html`](architecture.html) §4.

**Why split.** Two independent reasons, either sufficient on its own:

- *Independent verification.* A coder reviewing its own work rationalises its own choices — the review inherits the blind spots of the build. A separate review agent that did not write the code is adversarial by construction, not by prompt wording. This is the same logic that made `exec:review` fan out to distinct personas (the persona contract); phase separation extends it across the build/review boundary, not just within review.
- *Per-phase model economics.* The operator anchors review to a stronger, more expensive model regardless of what built the change — a cheap model codes, an expensive one reviews. That asymmetry is only expressible if each phase is its own `claude -p` spawn with its own `--model` pin. A single worker pins one model for the whole chain.

**Cost accepted.** Every phase boundary now pays a spawn plus artifact rehydration: the next phase starts cold and reads its inputs from the handoff envelope (architecture §5) rather than inheriting them in context. A single worker kept that context for free. This makes the handoff envelope load-bearing for *all* cross-phase state, not just `pr_urls` — the envelope must carry what each phase needs the next to know. This is the deliberate price of independence and the asymmetric-model economics.

**Alternatives considered.**

- *One worker runs the whole chain (status quo).* Rejected: it forecloses both independent review and per-phase model pinning — the coder grades itself, on the model that built the change.
- *One worker, but review re-spawned as a sub-agent within it.* Rejected as a half-measure: it buys independent review but not independent model economics at the phase grain, and it keeps the chain's lifecycle coupled to one outer session that the control plane cannot steer phase-by-phase (§10).

## 21. Review is multi-altitude; higher-altitude review yields new work, not reverts

Review fires at three altitudes matching the delivery hierarchy `shape:delivery` already produces — task, milestone, project — not only per task. The full model is in [`architecture.html`](architecture.html) §12.

**Why multi-altitude.** A task review is diff-bounded: it grades one issue's change against its AC and the quality lenses. It cannot see whether the tasks of a milestone *cohere*, whether landing them degraded something *outside* their own boundary, or whether the project met its stated goals. Those are real defect classes that only exist at a higher altitude, so they need a review oriented to that altitude. Verification rolls up the same tree the work was decomposed down — the dual of the delivery hierarchy.

**The load-bearing consequence: higher-altitude review produces new remediation work, not reverts.** Task review can halt before a PR merges, so its verdict can block a not-yet-merged artifact. A milestone or project review runs *after* its child PRs have merged; a merged slice cannot be cleanly reverted. So a failing milestone/project review emits new Linear issues slotted back into the plan — it does not roll back landed work. This is a genuinely different halt semantic from the task-level halt/revert contract (§3, §9-guardrails) and must be modelled as such: the supervisor's tree walker reacts to a failing altitude verdict by *scheduling*, not *reverting*.

**Two unknowns deferred to spikes, not decided here.**

- *Regression-review blast radius.* "Did landing this milestone degrade anything outside its boundary" is not diff-bounded the way task review is — it needs a definition of the boundary and probably a cross-cutting test/check run. Shape it with `shape:design` before building.
- *Remediation routing.* When an altitude review fails, what exactly is created (a new issue under the same milestone? a blocking flag on the project?), and does the supervisor pause the parent or keep draining siblings? This is the verdict-handoff open seam (architecture "Known open seam") widened to higher altitudes.

**Alternatives considered.**

- *Task review only; trust that coherent tasks compose into a coherent milestone.* Rejected: integration and regression defects are exactly the ones that survive a green per-task review, because no task-level lens is oriented to find them.
- *Run all altitude reviews inline at project end.* Rejected: a milestone defect found only at project end is far more expensive to remediate than one caught when the milestone closed, and project-end is too late to inform the next milestone's work.

## 22. The supervisor stays a process executing a planned unit; it is not a Claude skill

Two coupled decisions about the supervisor's form and scope.

**Scope — a planned unit, not specifically a cycle.** The supervisor executes a *planned unit of work*: a Linear cycle today, a whole project later. The execution atom is unchanged (one issue, a worker per phase, §20); a cycle and a project differ only as containers with a hierarchy over them (§21). "Drain a cycle" becomes one entry point rather than the definition. Project execution is out of scope now, but it is a later container on the same machinery, not a redesign — so vision and architecture are written in the generic terms ([`architecture.html`](architecture.html) §12). The tool keeps the name `drain-cycle`.

**Form — a process with a CLI front-door, not a `/execute-cycle` skill.** It is tempting to encapsulate the supervisor itself as a Claude skill (`/execute-cycle`, `/execute-project`) for one-command ergonomics. Rejected: a skill runs *inside* a Claude session, which makes the supervisor Claude-shaped and collapses the artifact boundary (§2) that lets the worker be any vendor. The supervisor's whole value is being content-blind and vendor-agnostic; a skill cannot be that. The one-command ergonomics come instead from a thin CLI front-door (`drain-cycle run <unit>`), while Layer 2 stays skills.

**Why this matters now.** The resident control plane (§10) is a long-lived process with an API — that only makes sense as a process, reinforcing the form decision. A control plane implemented as a Claude skill could not be the daemon that spawns and steers vendor-agnostic workers.

**Alternatives considered.**

- */execute-cycle as the primary entry point.* Rejected per above — collapses the vendor-agnostic boundary.
- *Hybrid: a `/execute-cycle` skill that shells out to the process for interactive use.* Not adopted now, but not foreclosed — it is a thin convenience wrapper over `drain-cycle run`, addable later if the keyboard ergonomics warrant it, without moving any supervision logic into the skill.

## 23. Responding to PR review feedback is a control-plane behaviour, not a pack skill

An earlier plan framed PR-feedback response as a Layer-2 pack skill (`/shape:pr-respond`, ABA-331) driven by an operator-launched foreground polling loop (`drain-cycle pr-feedback`, ABA-332). Both are cancelled. Responding to review comments is a **Layer-1 control-plane behaviour** of the resident daemon ([architecture.html](architecture.html) §9–§10, §10 here), because it requires watching a PR *after it is open* — a liveness only a resident process has. A pack skill runs inside one worker session bounded to a single diff; it cannot watch a PR across review rounds. The responsibility belongs to the daemon, not the pack.

**The idempotency contract (carried forward from the cancelled ABA-331).** The daemon must never address the same review comment twice across invocations. The run-log already carries the write target: `responder_runs[]`, an array of `{comment_ids[], invoked_at, result}` objects (shipped null/empty by ABA-321). "New" means a comment ID absent from every prior `responder_runs[].comment_ids[]` for the issue. A poll that finds no new comments exits cleanly without changes; running twice over the same comment produces neither a duplicate fix nor a duplicate log entry.

**Mechanism (deferred to Gear 3).** The concrete loop — read new comments → revise in the issue's worktree → re-submit to the stack → append to `responder_runs[]` — is the Gear 3 PR-review lifecycle in [`ideas/drain-past-the-merge-gate.md`](ideas/drain-past-the-merge-gate.md), gated behind the merge-gate evolution. This decision records *where the responsibility lives and why*; the gear doc owns the *how* and its open questions.

**Why this matters now.** PR-feedback response is part of the supervisor's autonomy horizon (§9): of the horizon behaviours, the merge-gate evolution is shaped but the feedback loop had only the two cancelled tickets to carry it. Recording the relocation keeps the behaviour — and the idempotency design it had already worked out — from being silently dropped when ABA-331/332 closed.

**Alternatives considered.**

- *Pack skill `/shape:pr-respond` + `drain-cycle pr-feedback` polling subcommand (the original plan).* Rejected: a skill is bounded to one worker session and one diff, so it cannot watch a PR after it is open; a foreground operator poller is a hand-cranked stand-in for the daemon's own liveness. Superseded by control-plane ownership.
- *Resolve wrong-fix escalation here.* Deferred: if the daemon's revision for a comment is itself wrong, the operator has no automated escalation path. Left as a Gear 3 open question rather than decided now.

## 24. The keystone cutover: `prompt.py` collapses to a pointer at `exec:pickup`

The architecture ([`architecture.html`](architecture.html) §3) describes Layer 1 as carrying no procedure: the worker's prompt points at the entry skill and the workflow lives in Layer 2. **Reaching that state in code is one concrete change — strip `prompt.py` from inlining the workflow down to a thin pointer at `exec:pickup`.** This is the keystone: the cutover that turns the two-layer split from a description into running code. Tracked by the "drain-cycle supervises; the pack owns the workflow" project.

This entry, not the architecture doc, is where the cutover's status lives — the architecture describes the structure as designed, "as is"; a migration that hasn't fully landed is decision-log material, not architecture.

**Current state and what the cutover changes.** Today `prompt.py` inlines the worker procedure as prose and names a few skills directly — the current `/code-review-and-quality` (review) and `/shape:pr-finishing` (finish). At the cutover those references swap to the `exec:*` namespace (`exec:review`, `exec:finish`; ADR 0004 / Shaper `execution-workflow` design doc), and the inlined procedure collapses to the single pointer. After it lands the supervisor names no workflow steps at all — only the entry skill — so any vendor's worker follows the same Layer-2 prose.

**Why it's the keystone.** The properties the architecture describes — the content-blind artifact boundary, dual-mode, vendor-agnostic workers — all rest on the supervisor not carrying procedure. This is why §16/§18's orchestrator-assembles-the-stack design was reversed in §19: hard-coding the `gt`/`gh` sequence into Layer 1 keeps procedure in the supervisor and blocks the cutover.

## 25. The stop-guard hook is injected at spawn via `--settings`, not the worktree's config

The orchestrator plants `.drain-guard.json` before each spawn and reads `.drain-guard-tripped` after (§ the stop-guard mechanism), but the Stop hook that increments the marker and writes the tripped file — `drain-cycle _stop-guard` — only ran when the target repo's own tracked `.claude/settings.json` happened to register it. In a repo that didn't (the common case), nothing invoked `stop_guard.run()`: the marker stayed at `count: 0`, no tripped file appeared, and a worker that stopped mid-completion exited cleanly with the work uncommitted. The guard designed to catch a premature stop was inert, so the run halted with the generic not-Done line and no signal that the work had been finished but not captured.

**Decision — `build_argv` always appends `--settings '<json>'` carrying the Stop hook.** The injected payload registers one Stop entry running the same guarded command the drain-cycle repo's own `.claude/settings.json` carries: `sh -c 'test -f .drain-guard.json || exit 0; command -v drain-cycle >/dev/null 2>&1 || exit 0; exec drain-cycle _stop-guard ...'`. It is a no-op unless the marker is present and the console script is on PATH, so it stays inert for non-drain sessions even though it is injected unconditionally. Claude Code concatenates `hooks` arrays across settings sources, so this entry runs *alongside* the symlinked project hook (e.g. entire's checkpointing, §10) rather than replacing it — no merge logic to maintain.

**Why `--settings` and not patch the worktree's `.claude/settings.json`.** Two failure modes the file route would introduce, both avoided by passing inline JSON that touches no file. First, §10 symlinks `.claude` into the worktree only when the repo gitignores it — so editing `worktree/.claude/settings.json` would write *through the symlink to the source repo's real config*. Second, in a repo that tracks `.claude` (no symlink), patching the file leaves a tracked-file modification the guard's own `_git_dirty` check would misread as uncaptured work — the guard tripping on its own wiring.

**Interaction with §10.** §10's earlier note ("Passing `--settings <repo>/.claude/settings.json` … leaves four other surfaces broken") rejected `--settings` as a *replacement* for the symlink — pointing it at a single file would drop `settings.local.json`, project agents/skills/commands, and `.mcp.json`. That reasoning stands; this decision is additive, not a reversal. The symlink still brings in the full project config; `--settings` adds one hook the supervisor needs present in *every* target repo regardless of what that repo tracks. The two compose: symlink for the repo's own tooling, `--settings` for drain-cycle's own guard.

**Known edge — drain-cycle draining its own repo.** Because hooks concatenate (§ above) and §10 symlinks `.claude` back to the source, a self-drain loads *both* the source `settings.json` Stop hook and the injected `--settings` hook — the guard command then runs twice per Stop event. On the block path that is idempotent (the same block reason is re-emitted). On the trip/increment path it double-increments `count`, so the guard could reach `max_blocks` in fewer real re-injections than intended. The window is narrow (only self-drains, only after the implementation is already incomplete) and the failure mode is "gives up one cycle early," not a wrong verdict — so it is left documented rather than coded around. The clean resolution, once every spawn injects the guard, is to drop the redundant Stop entry from this repo's own `.claude/settings.json`; that file is gitignored machine-local config, so it is an operator edit, not a tracked change.
