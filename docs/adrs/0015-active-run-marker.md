# ADR 0015: Active-run marker lives above `runs/` as `~/.drain-cycle/active.json`

**Date:** 2026-05-24
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §11

Without a live-run signal there is no way to distinguish a working run from a hung one: the run-log gains an entry only on issue completion, and the orchestrator emits only sparse stderr lines. The fix is an active-run marker — a small JSON file written before each spawn and removed in the worker's try/finally — that a second terminal can read with `drain-cycle status`.

**Why `~/.drain-cycle/active.json`, not inside `runs/`.** The `grade` command globs `runs/*.json` and groups files by `cycle_id`. A marker placed in `runs/` would either corrupt a grading run (if it looks like a run log) or require `grade` to skip it by sentinel field (fragile coupling). Placing the marker at `~/.drain-cycle/active.json` — one level above `runs/` — means `grade`'s glob never sees it and the two concerns share no code path.

**Why not `runs/active.json`.** Same issue: inside `runs/` it's in the glob's scope. A separate directory (`~/.drain-cycle/live/`) was considered but adds a layer without benefit; a single well-named file at the parent level is enough.

**Why atomic write (temp-file rename).** `drain-cycle status` reads the marker from a different process, potentially mid-write. `Path.write_text` is not atomic: the file is truncated before the new content is written, so a reader arriving between those two steps sees an empty file. Rename is atomic on POSIX filesystems: the reader sees either the old complete content or the new complete content, never a partial write. The temp file uses a `.tmp` extension adjacent to the marker (`active.json.tmp → active.json`), not a different directory, so the rename is always within the same filesystem mount.

**Why the progress block is updated on every new turn, not on every raw JSON line.** The worker's stream-json output emits one event per content block per turn (thinking, text, tool_use) — a single turn with a thinking + tool_use block produces two events carrying the identical usage. Firing the callback on every raw event would write the file multiple times per turn with the same data, wasting I/O and producing redundant stderr lines. The reader thread deduplicates by message id: the callback fires once per unique message id, which is once per turn. The first event in a turn records the turn; subsequent events with the same id are no-ops for the callback.

**Stale marker detection.** A crash or SIGKILL leaves the marker on disk (the try/finally doesn't run on SIGKILL). `drain-cycle status` checks `os.kill(pid, 0)` — if the pid is gone it reports a stale marker rather than a live run. It does not delete the marker automatically; the operator removes it with `rm`, preserving forensic evidence of the interrupted run.
