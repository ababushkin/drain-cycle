# ADR 0009: Each issue declares its target repo via a `repo:<name>` Linear label

**Date:** 2026-05-23
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §5

The orchestrator used to be single-repo by construction: `repo = Path.cwd()`, every worktree under `<cwd>/.worktrees/`. Cycles in this workspace span multiple repos by design — `linear-workflow.md` makes "Affected repos" part of the six initiative-readiness fields, and the Ops slot deliberately holds cross-repo maintenance issues. Each issue now carries a `repo:<name>` Linear label; `~/.drain-cycle/repos.yml` maps the name to an absolute path; the operator runs `drain-cycle` from anywhere.

**Grouped labels are supported.** The Linear workspace uses label groups (`repo`, `model`, `wave`, …). A child label in the `repo` group — e.g. the leaf `drain-cycle` under the `repo` group — is indistinguishable from a flat `repo:drain-cycle` label to `repos.resolve`. The `pending_issues` query fetches `labels { nodes { name parent { name } } }` and renders each grouped node as `"<group>:<name>"` via `_label_name`; ungrouped nodes keep their bare name (backward-compatible with any literal `repo:<name>` labels already in use). The same rendering applies to `model` group children for `model.resolve` (ADR 0011).

**Alternatives considered.**

- *Description-body encoding* (e.g. a `Repo: drain-cycle` line in the markdown). The description is the most-edited surface, agents rewrite it routinely, and there's no schema enforcement. A label is one structured field with one value — Linear validates it, and a label rename triggers a clear "this label doesn't exist" error rather than silently drifting to a wrong repo.
- *Title prefix* (e.g. `[drain-cycle] Fix the foo`). Cleaner machine parse than the body, but every issue's title gains visual clutter for a parser-only concern. Labels don't pay that cost.
- *Inherit from the Linear project's "affected repos"*. Ambiguous for projects that legitimately touch multiple repos, and there is no obvious answer for the Ops container project, which is multi-repo by definition.

**Missing-repo halt behaviour.** Same machinery as every other pre-spawn halt: write a run-log entry, print the `Halt:` line to stderr, exit 1, leave subsequent issues untouched. The new wrinkle is that resolution failures happen before any Linear state is moved, so they skip the post-spawn revert path (`_revert_to_pre_halt_state`). This is the difference from the existing setup-failure halt: that one happens after `worktree.add` or the initial `linear.set_state` attempt; the resolution halt happens before either. Either way, no revert is needed because no state was moved.

**`repos.yml` config errors are even earlier.** A missing or malformed `repos.yml` halts the CLI at startup, before any Linear traffic and before the run-log file is created. There is no cycle yet to log against, so the failure surfaces only on stderr. This is enforced eagerly in `cli.main` so the orchestrator never sees a broken config.

**Why labels over file conventions.** (e.g. requiring the operator to put the issue identifier in a branch comment, or use a remote-name convention). Labels are the only signal that's enforceable in Linear's UI: cycle planners can see at a glance which repo an issue targets, and the multi-repo distinction is visible at the right surface (Linear) rather than buried in a config file.

**Out-of-v1 deliberately.** No env-var expansion inside `repos.yml`; no auto-clone if the path is missing; no parallelism across repos; no retroactive labelling of pre-cycle issues. All are operator-time concerns rather than tool-time concerns. Multi-team Linear support stays out of scope too — the tool is still hardcoded to the `Personal` team.
