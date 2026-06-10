# ADR 0001: Integration test architecture

**Date:** 2026-06-02
**Status:** Accepted

## Context

drain-cycle only tests against a real Linear cycle. Scenario-level integration tests that cover failure modes, resume semantics, and usage tracking require a controllable Linear surface. Without an override seam, every failure-mode test needs either a real Linear cycle or invasive monkey-patches — both make the suite cost-prohibitive to maintain. See `docs/integration-test-architecture.md` for the full design.

## Decision

**Parent HTTP mock + Linear-MCP shim + real `claude -p` + in-process driver.**

Three pillars:

1. **`MockLinear`** — a FastAPI/Starlette GraphQL fake on a random localhost port, started per-test. Fault injection is per-operation, per-call-index. Both the parent and the shim write into one shared in-memory store, so tests assert against a single call log.

2. **`linear_mcp_shim`** — a stdio MCP server backed by the same in-memory store. Translates the spawned `claude -p` child's MCP tool calls (`save_issue`, `save_comment`) into GraphQL POSTs against `MockLinear`. Registered in each test worktree's `.mcp.json` under the `linear` server name.

3. **`IntegrationHarness`** — a pytest fixture that redirects `HOME`, starts `MockLinear`, populates it with scenario data, sets `LINEAR_API_URL`, and calls `orchestrator.run()` in-process.

The seam that makes pillar 1 work: `LINEAR_API_URL` env var in `drain_cycle/linear.py` — read at call time inside `_post`, so `monkeypatch.setenv` takes effect without module reload. The default URL is unchanged when the env var is unset. No new abstraction layers.

## Alternatives considered

**Scripted/fake `claude -p`.** Cheaper, faster, deterministic. Rejected: a scripted child stops being an integration test — it cannot validate the real session's usage stream, MCP tool calls, prompt-to-completion behaviour, or production OTel spans against actual data.

**Hybrid: real claude on happy-path only; scripted child for failure scenarios.** Two patterns to maintain; weaker default signal; gives up real-claude coverage on usage-tracking edge cases.

**Function-injection / `LinearClient` protocol on the parent.** Skips the HTTP boundary and adds an abstraction layer that earns nothing else. The existing module-level functions in `linear.py` drive against an HTTP fake once `LINEAR_API_URL` is overridable — no new protocol needed.

**VCR-style recorded fixtures.** Realistic but awkward for failure injection; expensive to maintain under Linear schema drift.

## Consequences

- Real `claude -p` makes the suite costly (~$0.05–$0.20/scenario) and slow (~15–30s/scenario). Scenarios that spawn claude carry `@pytest.mark.real_claude` and are excluded from the default `pytest` invocation.
- The `LINEAR_API_URL` seam is the minimal production change: one env-var read in `_post`, guarded against module-load caching by being resolved inside the function.
- The official Linear MCP server cannot be redirected via `LINEAR_API_URL` — it speaks MCP over stdio, not raw GraphQL HTTP. The shim is the smallest viable seam for the child's Linear calls.

## Revisit conditions

- If `MockLinear` schema drift becomes a maintenance burden, consider generating the mock from Linear's published GraphQL schema.
- If `real_claude` suite cost consistently exceeds $5/run, revisit the hybrid approach for breach and failure scenarios.
- If a future version of the official `linear-mcp` server exposes a redirect-able endpoint, drop the `linear_mcp_shim` pillar.
