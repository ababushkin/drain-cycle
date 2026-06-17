# ADR 0008: Run-log is one file per invocation, not one file per cycle

**Date:** 2026-05-22
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §4

Each `drain-cycle` invocation writes its own run-log file at `~/.drain-cycle/runs/<cycle-id>-<run-timestamp>.json`. The per-file schema is unchanged — `{cycle_id, cycle_duration_seconds, entries: [...]}` — and `cycle_id` inside each file is how downstream readers group runs of the same cycle.

**Alternatives considered.** (A) Multi-run schema in one file: `{cycle_id, runs: [...]}`. Faithful, but every reader has to learn the new shape and the on-disk backup file needs migrating. (B) Open-and-extend: load the existing file and append to a single flat `entries` list. Loses run boundaries, and `cycle_duration_seconds` (computed as `max(finished_at) - min(started_at)`) spans the inter-run gap and becomes misleading. (D) Refuse to clobber: fail-fast if the file exists. Breaks the unattended re-run flow the tool exists for (fix X, re-run, drain the rest) until a resume mode is built.

**Why per-run files.** The bug being fixed (ABA-230) is that a second invocation against the same cycle silently overwrites the first run's data. Per-run files make the write path single-writer-write-once — no read-then-write race, no schema diff, no migration. US-D (ABA-197), which already plans to glob `runs/*.json` and merge across cycles, gets a one-line addition (group by `cycle_id`) instead of a new shape to read. Each file's `cycle_duration_seconds` represents one invocation's hands-off time; US-D sums them when reporting cycle-level KR2.

**Cost.** A re-run cycle accumulates one file per invocation. At cycle scale (≤ 15 issues, rarely more than 2–3 invocations to drain) this is negligible. No retention policy is shipped; trim by hand if it ever matters.
