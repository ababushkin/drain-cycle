# Linear initiative — multi-agent collaboration for correctness

**Six agent-driven roles take a Linear ticket from intake to merged PR — so hard tickets don't silently ship with missing AC.**

**The bet.** drain-cycle today drains a Linear cycle unattended — one ticket, one worker, one diff. The next step: six agent-driven roles operating end-to-end on the same ticket — task-shaping before code, outcome verification before Done, structured PR preparation, and autonomous handling of human review feedback. Same operator, same tickets, less manual shepherding.

**What we'll know in 5 hard tickets.** Of the next 5 hard tickets drained under the new flow, the bet is ≥ 3 ship and merge with no operator-pushed fix commits. Otherwise the kill condition triggers (§5) and contract enforcement reverts.

---

## 1 The problem and the bet

**Why this matters beyond drain-cycle.** drain-cycle is the batch driver for a growing toolbox of composable skills and MCPs. Each new skill compounds across both modes of use — at the keyboard during interactive work, and inside a worker session during a drain. This initiative ships the next four skills plus two CLI subcommands the toolbox needs to take a hard Linear ticket from intake to a merged PR, plus the durable contracts (run-log fields, Linear-issue dual-write, OTel attrs) that make their outputs inspectable afterwards.

drain-cycle evolves from "drain a Linear cycle unattended" toward a **UNIX-philosophy dispatcher for composable skill primitives** — Linear tickets as the work queue, skills+MCPs as the small tools, and `drain-cycle` itself as the batch-mode shell that composes them. The operator (you) drives the *why* (initiative goal + KRs) and the *what* (outcome-focused Linear issue); agents handle the *how* through a defined set of skills. The same skills run manually at the keyboard or automatically inside a worker session — design decision §10's symlink makes this true.

The operator failure mode this initiative addresses: **complex tickets that can't be sliced further** where the worker forgets details, under-verifies, or submits an unstructured PR — followed by human review comments piling up while the operator manually re-invokes the agent each round. Three concrete failures today:

1. **Silent Done on hard tickets.** Complex unsliceable tickets get drained; the worker self-asserts Done; the diff misses AC; the operator finds out at PR review and fixes by hand. *(e.g., a ticket asks for "halt the drain if Linear label resolution returns ambiguous matches"; the worker ships the label-parse change but never adds the halt logic; the diff passes self-review because nothing it touches asserts the halt-on-ambiguity contract.)* Left unaddressed, the unattended-drain promise breaks on the tickets where it matters most.
2. **Unstructured PR submission.** PRs land without What/Why/Focus guidance; the reviewer has to guess what to look at, or the operator manually rewrites the body. *(e.g., a multi-file refactor lands with a subject line and no body; the reviewer reads the diff blind to find out whether the risky bit is in `labels.py` or `worker.py`.)* Left unaddressed, PR review burden stays entirely on the human — auto-mergeable changes get the same scrutiny as risky ones, and review throughput suffers.
3. **Stalled feedback loops.** Human review comments accumulate on stacked PRs; the operator manually re-invokes claude each round. *(e.g., a stacked PR gets three rounds of review comments across two days; each round the operator opens the worktree, re-launches claude, and shepherds the fix manually.)* Left unaddressed, the agent ecosystem stops at code-submission time instead of running end-to-end through merge.

The bet: six agent-driven roles end-to-end, each backed by a skill (or CLI subcommand) fitting the UNIX-philosophy frame. A drained ticket flows through the pipeline below; the per-role detail table follows.

```
┌─ Operator (you) ──────────────────────────────────────────┐
│  throughout · defines why (KRs) + what (Linear ticket)    │
└──────────────────────────────┬────────────────────────────┘
                               ▼
┌─ Task Shaper ─────────────────────────────────────────────┐
│  before code · /shape:task                                │
│  → enriched AC + sizing decision + per-stack task list    │
└──────────────────────────────┬────────────────────────────┘
                               ▼
┌─ Implementer ─────────────────────────────────────────────┐
│  middle · 6 existing build/test/review/commit skills      │
│  → code + tests + in-session code review                  │
└──────────────────────────────┬────────────────────────────┘
                               ▼
┌─ Outcome Verifier ────────────────────────────────────────┐
│  after impl · /shape:verify-implementation                │
│  → pass/fail verdict in run log (gates Done)              │
└──────────────────────────────┬────────────────────────────┘
                               ▼
┌─ PR Preparer ─────────────────────────────────────────────┐
│  before submit · /shape:pr-prepare                        │
│  → What/Why/Focus body OR auto-merge label                │
└──────────────────────────────┬────────────────────────────┘
                               ▼
┌─ PR Responder ────────────────────────────────────────────┐
│  on review feedback · /shape:pr-respond                   │
│  (operator launches via `drain-cycle pr-feedback`)        │
│  → fix lands on stack · comment IDs tracked               │
└──────────────────────────────┬────────────────────────────┘
                               ▼
┌─ Grader ──────────────────────────────────────────────────┐
│  post-drain · `drain-cycle grade-draft <issue>`           │
│  → ~/.drain-cycle/grades/<issue>.md                       │
│    (status: draft → operator confirms)                    │
└───────────────────────────────────────────────────────────┘
```

| # | Role | Phase | Skill / CLI | Status |
|---|---|---|---|---|
| 0 | **Operator** (you) | Throughout | Operator-side skill table; defines *why* + *what* | — |
| 1 | **Task Shaper** | Before code | `/shape:task` *(complements `/shape:planning-and-task-breakdown`)* | [R] |
| 2 | **Implementer** | Middle | 6 existing skills (incremental-implementation, test-driven-development, source-driven-development, code-review-and-quality, git-workflow-and-versioning, debugging-and-error-recovery) | [E] |
| 3 | **Outcome Verifier** | After implementation, before Done | `/shape:verify-implementation` *(new)* | [N] |
| 4 | **PR Preparer** | After Outcome Verifier passes, before PR submission | `/shape:pr-prepare` *(new)* | [N] |
| 5 | **PR Responder** | On new human review comments (operator-launched polling loop) | `/shape:pr-respond` *(new)*, launched via `drain-cycle pr-feedback` subcommand | [N] |
| 6 | **Grader** (post-drain) | After worker session finishes | `drain-cycle grade-draft <issue>` CLI subcommand | [—] |

Roles 1 and 3 are a **complementary pair**: `/shape:task` defines the outcome-shaped task list at the start; `/shape:verify-implementation` validates the outcome at the end. Roles 4 and 5 are the **PR-side pair**: Preparer shapes outbound PRs, Responder addresses inbound review feedback. Role 6 is operator-confirm scaffolding for grading the initiative against its KRs.

**If the initiative is built:**

- **Hard tickets clear AC before they land.** The Outcome Verifier sees the diff before Done is recorded; the verdict goes to the run log; halt-on-fail leaves the worktree intact for operator inspection rather than producing a silent partial.
- **PR review starts with a structured map.** Reviewers open a PR and see What / Why / Focus from the start; auto-mergeable changes route past human review correctly; risky changes get the human attention they earn. Review throughput rises because reviewers no longer parse intent from the diff alone.
- **Human review feedback closes its own loop.** The operator launches `drain-cycle pr-feedback`; new review comments get addressed on the right stack node; comment IDs are tracked to prevent double-handling; the loop continues until the operator stops it. The agent ecosystem runs end-to-end through merge rather than stopping at submit-time.

**Constraints:**

- Subscription auth only (Claude Code / Codex CLI subprocess) — no API-key models. GitHub webhooks are free, but the agent can't run inside GitHub Actions; agent work happens on the operator's machine.
- Correctness > throughput. No parallel drains. No throughput-focused changes.

## 2 Affected repos

- **`drain-cycle`** — primary. Worker integration of roles 1–5, run-log schema extensions, `verify` label mechanism, CLI subcommands (`grade-draft`, `grade --flow=verify`), supersession of design decision §1, OTel attribute additions.
- **`agent-skills-shaper`** — secondary. Receives one per-ticket task-shaping skill (`/shape:task`, complementing the whole-project `/shape:planning-and-task-breakdown`) and three new skills (`/shape:verify-implementation`, `/shape:pr-prepare`, `/shape:pr-respond`).

## 3 Goal and Key results

The KRs are plain English — a non-pack-author should grade each in 30 seconds.

### 3.0 Goal

**For** the operator running drain-cycle on outcome-focused Linear tickets, **we want** six agent-driven roles that take work from ticket to merged PR — task-shaping before code, outcome verification before Done, structured PR preparation, and autonomous response to human review comments — **so that** complex tickets don't silently land with missing AC, PRs are reviewable without guesswork, and human review feedback doesn't create a manual re-invocation tax.

### 3.1 KR1 (stretch) — Of the next 5 hard tickets drained with the new flow, ≥ 3 ship and merge with no operator-pushed fix commits *(bet)*

- **plain-English version:** when I run drain-cycle on a hard ticket I couldn't break into smaller pieces, six agent roles take it from intake to merged PR. The bet: this catches enough real problems and structures the PRs well enough that most merge cleanly without me pushing fix commits.
- **baseline:** 0 of 0 — none of the six roles exist as a connected flow today.
- **target:** ≥ 3 of the next 5 hard tickets drained with the new flow ship and merge with no operator-pushed fix commits.
- **measured over:** the first 5 hard tickets drained with the new flow (~2 cycles at current cadence).
- **how we'll grade it:** per-ticket grade markdown at `~/.drain-cycle/grades/<issue>.md` (auto-drafted by `drain-cycle grade-draft <issue>`, operator confirms `draft` → `confirmed`); `drain-cycle grade --flow=verify` reports the rolled-up pass-rate across the window.

### 3.2 KR2 (commit) — Every ticket reaching Done in Linear under the new flow has a recorded Outcome Verifier verdict in the run log; zero exceptions *(brake)*

- **plain-English version:** the worker no longer marks its own ticket Done. The contract: a ticket using the new flow reaches Done only if the run log shows the Outcome Verifier ran and produced a verdict. Protects against the failure mode where the agent thinks it's finished but missed half the AC.
- **baseline:** silent self-assertion currently possible (per design decision §1); the count of past silent Dones is unknown because the recording fields don't exist yet.
- **target:** 0 tickets reach Done in Linear under the new flow without an `outcome_verdict` recorded in the run log.
- **measured over:** every cycle running the new flow, for the initiative's duration (cap at 10 cycles if the initiative runs long).
- **how we'll grade it:** `drain-cycle grade` fails any entry where `flow == "verify"` AND `final_linear_state == "completed"` AND `outcome_verdict` is absent. Exit code ≠ 0 on any violation. One violation in any cycle fails the KR — this is a brake, not a rate.

### 3.3 KR3 (commit) — Every PR submitted under the new flow has the structured What / Why / Focus body, or is correctly routed to auto-merge *(brake)*

- **plain-English version:** the PR Preparer either writes a structured What / Why / Focus body and routes the PR for human review, or correctly identifies the PR as auto-mergeable (no critical focus items) and routes it to auto-merge. Either way, every PR ships with a documented routing decision and a reviewable body.
- **baseline:** today, PRs from drained tickets carry whatever body the worker produced — typically a one-line subject and no review guidance.
- **target:** 0 PRs submitted under the new flow without either (a) structured What/Why/Focus body OR (b) auto-merge label with PR Preparer reasoning attached.
- **measured over:** every PR submitted under the new flow, for the initiative's duration.
- **how we'll grade it:** PR Preparer writes its decision to a `prep_verdict` field on the run-log entry; `drain-cycle grade` fails any verify-flow entry where `prep_verdict` is absent or where the verdict says "structured body" but the PR body is empty or single-line.

The PR Responder (role 5) gets no KR of its own — KR1 captures it structurally (clean-merge rate degrades if the responder doesn't address review feedback).

## 4 Appetite

**~18–22 issues across five milestones.** Each milestone ends with a review gate — the operator reviews progress and either continues to the next milestone or pauses for a reshape/kill decision if something clear has surfaced. The gate default is review-and-continue, not stop-the-line. Whole-initiative kill conditions are separate (§5).

### M1 — Recording infrastructure

Build the data layer first, behaviour later.

- `verify` Linear label parser added alongside `repo:` / `model:` resolution in `labels.py`.
- Run-log schema fields: `flow` (string, e.g. `"verify"`, or `null` for default flow), `outcome_verdict` (object: `{result, findings[], invoked_at}` or `null`), `prep_verdict` (object: `{result, route, reasoning}` or `null`), `responder_runs[]` (array of objects: `{comment_ids[], invoked_at, result}` — appended each time the responder runs).
- Matching attributes on the `drain.issue` OTel span.
- `docs/design-decisions.md` §1 supersession: write the successor decision explaining the verifier-gated Done contract; mark §1 as superseded.

**Gate:** an end-to-end `verify`-labelled ticket drains; new fields populate (all `null` since no roles are wired yet); existing behaviour unchanged.

### M2 — Worker-internal loop (Task Shaper, Outcome Verifier, halt path)

- **Task Shaper** (`/shape:task`): per-ticket task-shaping skill that takes a Linear issue and produces an enriched AC checklist + sizing decision (single stack vs N stacks) + per-stack task list. Distinct from `/shape:planning-and-task-breakdown` (whole-project / design-doc shaping); the two coexist in the toolbox at different scopes. The precise interface is resolved in design decision §17 (`docs/design-decisions.md`): `/shape:task` runs inside the worker session at implementation time; `/shape:planning-and-task-breakdown` stays at design-doc scope and is not extended to emit per-ticket artefacts.
- **Where the sizing decision lands**: dual-write. (a) Fed into the implementer in-session as context; (b) appended to the **Linear issue body** so future resumes / pickups carry the full context without re-running the Task Shaper. The Linear write is the durable artefact.
- **Implementer integration**: worker session pulls the Task Shaper output as context; no other implementer changes.
- **Outcome Verifier** (`/shape:verify-implementation`): new skill. Inputs: ticket body + Task Shaper output + diff + run-log. Output: structured verdict (pass / fail + findings) written to `outcome_verdict`.
- **Halt-on-fail path**: extends decision §8 / §9 machinery — on Outcome Verifier fail, leave worktree, halt cycle, name the breach in `cycle_halt_reason`.

**Gate:** 3 verify-labelled hard tickets drained; verdicts recorded; halt path exercised at least once on a deliberate failure (e.g. a synthetic ticket whose AC the implementer can't satisfy).

### M3 — PR-shape gate (PR Preparer)

- **PR Preparer** (`/shape:pr-prepare`): new skill. Reads stack PRs via Graphite MCP; writes What / Why / Focus body on each; decides auto-merge vs human-review per the carve-out rule (small + no human review needed → auto-merge; otherwise → human-review).
- **Auto-merge mechanism**: Graphite-native (`gt submit --merge-when-ready`-equivalent).
- Records the decision to `prep_verdict` on the run-log entry.

**Gate:** 3 stacks emitted, all with structured What / Why / Focus bodies or auto-merge labels. PR-body assertion in `drain-cycle grade` passes on all three.

### M4 — PR-feedback loop (PR Responder)

- **PR Responder** (`/shape:pr-respond`): new skill. Reads new review comments on stack PRs via Graphite MCP; makes the fix in the right worktree; pushes to the stack; appends to `responder_runs[]`.
- **Trigger model**: a separate operator-launched subcommand — `drain-cycle pr-feedback`. The operator launches it explicitly; the command then runs as a polling loop, addressing PR comments as they arrive, until the operator stops it. Not invoked implicitly at drain-start, not a GitHub Action, not a boot-time background daemon. A foreground polling agent whose lifetime the operator owns.
- **Idempotency**: each comment ID tracked in `responder_runs[].comment_ids[]` so the same comment isn't addressed twice across `pr-feedback` invocations.

**Gate:** one real PR with operator-left review feedback gets addressed end-to-end by the responder; the fix lands on the stack; the run-log shows the responder run.

### M5 — Grading + cost + close-out

- **Per-ticket grade template** at `~/.drain-cycle/grades/<issue>.md` with checklist + `status:` field.
- **`drain-cycle grade-draft <issue>` CLI subcommand**: one-shot grading prompt. Runs automatically on drain completion (writes `status: draft`); also runs manually for retro-fill on past tickets.
- **`drain-cycle grade --flow=verify` subcommand**: reads `confirmed` grade files, reports pass-rate across a ticket window, warns about un-confirmed drafts.
- **Silent-Done detection** (KR2 grader): exit nonzero on any `flow == "verify"` AND `final_linear_state == "completed"` AND `outcome_verdict == null` entry.
- **Cost-impact handling**: measure per-ticket token multiplier under the new flow; add `verify`-flow-specific overrides in `limits.yml` if cycle cap pressure surfaces.
- **Post-launch review**: write a Linear comment on each KR's status at the end of the initiative window; either ship cleanly, pause, or kill.

**Gate:** KR grading runs end-to-end; one of {ship cleanly, pause, kill} is decided and recorded.

## 5 Kill condition

**Primary kill (whole-initiative):** after running the new flow on 5+ hard tickets across 2 cycles, the Outcome Verifier produces zero true-positive findings (every halt was a false positive, and verifier-passed Dones don't visibly improve PR-review outcomes vs default-flow drains).

**Secondary concerns (watched at each milestone's review gate, not auto-kills):**

- M1 — schema additions break run-log readers or downstream tooling.
- M3 — PR Preparer's bodies don't measurably reduce reviewer cognitive load (signal: the operator subjectively prefers the raw worker-produced PRs).
- M4 — PR Responder either (a) requires operator intervention to address comments cleanly >50% of the time, or (b) drops or misses comments in practice.

Any of these surfacing at a milestone review prompts a discussion (continue / reshape / kill), but the default is continue. Only the primary kill above is automatic.

**Action on kill:** revert the contract enforcement (the `verify` label becomes a no-op or is removed); keep the recording fields — they stay in run log + OTel because they're cheap and useful for future investigation; record the negative result as a new decision in `docs/design-decisions.md`.

## 6 Out of scope (explicitly)

- **Parallel drains.** Decision §8's serial-drain stance unchanged.
- **Replacing Graphite.** The PR-side work assumes the Graphite stack flow; switching PR-management tools is a separate decision.
- **Multi-operator / multi-tenant workflows.** Single-user product per `AGENTS.md` ("Python, single-user, personal product").
