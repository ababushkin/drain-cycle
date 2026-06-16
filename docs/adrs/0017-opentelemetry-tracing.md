# ADR 0017: Opt-in OpenTelemetry tracing to Honeycomb

**Date:** 2026-05-26
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §13

A drain runs unattended and can take hours, spending real tokens across many spawned sessions. The run log records the outcome per issue, but it is one flat file per invocation — it can't show where time went inside a drain, how the Linear round-trips and worker sessions nest, or let an operator aggregate cost across drains. Tracing fills that gap.

**Decision.** Each invocation emits one trace: a `drain.cycle` root span, a `drain.issue` span per attempted issue, and under those the `drain.worker.session`, `drain.worktree.add`/`.remove`, and per-operation `linear.*` spans. The `httpx` transport is auto-instrumented, so every Linear GraphQL POST appears as a child of its `linear.*` span. Worker token/cost/turn usage, the issue's repo/model/final-state, and the cycle outcome ride as span attributes; every halt site is tagged with a static `exception.slug` (greppable, low-cardinality, safe to `GROUP BY`). `service.name` is `drain-cycle`, which is also the Honeycomb dataset.

**Opt-in via `HONEYCOMB_API_KEY`.** The key's presence in the environment is the on/off switch. Absent, `telemetry.setup()` is a no-op and the default no-op tracer stays installed — a drain with no telemetry configured behaves exactly as before, takes no new network dependency at runtime, and the `start_as_current_span` calls scattered through the code cost nothing. The OTel packages are unconditional install-time dependencies (lightweight, pure-Python); only *exporting* is gated.

**Flush-on-exit is load-bearing.** `drain-cycle` is a short-lived CLI that exits through `sys.exit`. A `BatchSpanProcessor` buffers spans and exports on a timer, so without an explicit flush the queued spans die with the interpreter and the last issues of a drain never ship. `setup()` registers `shutdown()` (which flushes the processor) with `atexit`; `SystemExit` still runs `atexit` handlers, so every exit path drains the queue.

**Alternatives considered.**

- *`opentelemetry-instrument` zero-code agent.* Rejected: it wraps a `python` invocation, but the tool ships as a `uv tool` console-script entry point (`drain-cycle`), so there is no `python app.py` to wrap. Programmatic setup in `telemetry.setup()` is reliable regardless of how the entry point is launched.
- *Always-on tracing.* Rejected: it would force an exporter and an egress dependency on an operator who hasn't asked for it, and fail noisily (or silently retry) when Honeycomb is unreachable. Opt-in keeps the default path dependency-free.
- *Metrics and logs alongside traces.* Out of scope. The run log already covers durable per-issue accounting; traces add the causal/nesting view. A metrics layer can be added later if cost-rate alerting is wanted (see the otel-instrumentation layering guidance).
- *A span per private helper (e.g. `_plan`, `link_project_config`).* Rejected as over-instrumentation: those are fast, pure, and not independently aggregable. Interesting, failure-prone, or aggregable operations get spans; the rest stay as attributes on their parent.
