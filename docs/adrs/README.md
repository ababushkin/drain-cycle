# Architecture decision records

Design rationale for `drain-cycle`, one decision per file. Read these before making architectural changes — `AGENTS.md` points here.

Each ADR serves the project's guiding vision, [`../vision.md`](../vision.md), and realizes the architecture that serves it, [`../architecture.html`](../architecture.html) — the two-layer supervisor/workflow split on an artifact boundary. Each decision should hold the vision as its frame; a decision that no longer fits it is the signal to revisit the vision deliberately, not to drift from it silently.

ADRs 0003 and 0004 are intentionally unused locally — those numbers name the pack repo's (agent-skills-shaper) ADRs, cited from [`0002-thin-supervisor-contract.md`](0002-thin-supervisor-contract.md).

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-integration-test-architecture.md) | Integration test architecture | Accepted |
| [0002](0002-thin-supervisor-contract.md) | Thin-supervisor contract — prompt-segment allocation, handoff schema v2, process/workflow boundary | Accepted |
| [0005](0005-agent-self-updates-linear.md) | The spawned agent updates Linear itself | Accepted |
| [0006](0006-accept-skip-permissions.md) | `--dangerously-skip-permissions` is accepted | Accepted |
| [0007](0007-worktree-per-issue.md) | Fresh worktree per issue, not a shared workspace | Accepted |
| [0008](0008-run-log-per-invocation.md) | Run-log is one file per invocation, not one file per cycle | Accepted |
| [0009](0009-repo-label-targets-repo.md) | Each issue declares its target repo via a `repo:<name>` Linear label | Accepted |
| [0010](0010-uv-tool-install-and-secret.md) | Installed as a `uv tool`, with the secret read from `~/.drain-cycle/.env` | Accepted |
| [0011](0011-worker-model-default-and-override.md) | Workers default to Sonnet; a `model:` label overrides per issue | Accepted |
| [0012](0012-stream-json-usage-accounting.md) | Workers use stream-json output; usage is parsed from the wire | Accepted |
| [0013](0013-resource-guardrails.md) | Resource guardrails — a native cost belt and orchestrator token/time suspenders | Accepted |
| [0014](0014-worktree-config-symlink.md) | Headless workers inherit project-scoped config by symlink | Accepted |
| [0015](0015-active-run-marker.md) | Active-run marker lives above `runs/` as `~/.drain-cycle/active.json` | Accepted |
| [0016](0016-execution-order-blocks-aware.md) | Execution order — manual drag-order only, blocks-aware | Accepted |
| [0017](0017-opentelemetry-tracing.md) | Opt-in OpenTelemetry tracing to Honeycomb | Accepted |
| [0018](0018-resume-on-rerun.md) | Halted issues resume on re-run by reusing the preserved worktree | Accepted |
| [0019](0019-watch-in-tmux-pane.md) | `--watch` runs claude *in* the tmux pane, not a formatter tailing a log | Accepted |
| [0020](0020-graphite-stacking-sequence.md) | The Graphite PR-stacking sequence the orchestrator will run | Superseded by [0023](0023-worker-owns-pr-submission.md) |
| [0021](0021-shape-task-in-worker.md) | `/shape:task` runs inside the worker session | Accepted |
| [0022](0022-pr-links-recorded.md) | PR links are recorded in the run-log and posted to Linear by the orchestrator | Superseded by [0023](0023-worker-owns-pr-submission.md) |
| [0023](0023-worker-owns-pr-submission.md) | The worker owns PR submission via the finishing skill; the orchestrator reads `pr_urls` back | Accepted |
| [0024](0024-worker-per-phase.md) | A worker per phase, not a worker per issue | Accepted |
| [0025](0025-multi-altitude-review.md) | Review is multi-altitude; higher-altitude review yields new work, not reverts | Accepted |
| [0026](0026-supervisor-is-a-process.md) | The supervisor stays a process executing a planned unit; it is not a Claude skill | Accepted |
| [0027](0027-pr-feedback-control-plane.md) | Responding to PR review feedback is a control-plane behaviour, not a pack skill | Accepted |
| [0028](0028-keystone-cutover.md) | The keystone cutover — `prompt.py` collapses to a pointer at `exec:pickup` | Accepted |
| [0029](0029-stop-guard-via-settings.md) | The stop-guard hook is injected at spawn via `--settings` | Accepted |
| [0030](0030-execution-state-file.md) | The supervisor guarantees the execution state; each skill writes its own section | Accepted |
| [0031](0031-scorecard-correctness-contract.md) | The scorecard correctness contract — outcome pass AND review GO | Accepted |
| [0032](0032-non-gating-active-marker.md) | The `_active` step/persona marker is read for display only | Accepted |
| [0033](0033-project-drain-identity.md) | A project drain overloads `cycle_id`; it does not rename the field | Accepted |
