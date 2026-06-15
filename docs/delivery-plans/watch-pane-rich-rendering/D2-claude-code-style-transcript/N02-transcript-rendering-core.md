---
layer: node
id: N02
type: story
title: Transcript rendering core
parent: D2
serves_kr: KR1
maps_to: linear-issue
external_window: none
completion:
  form: acceptance-criteria
  criterion: >
    The pane shows ⏺ bullets for prose and tool calls (each tool with its one meaningful
    argument), ⎿ result previews, and ✻ thinking markers, width-truncated to the pane,
    with turn dedup preserved.
delegates_to: at-pickup task breakdown (story)
---

# N02 — Transcript rendering core

**Type:** `story`.

> **Blocked by:** [N01](../D1-filter-never-breaks-the-pane/N01-crash-proof-filter.md) — Graphite
> stack PR 2 sits on the hardened filter.

## What

As the drain-cycle operator, I want the pane to render each event the way interactive Claude Code
does — ⏺ bullets for assistant prose and tool calls, ⎿ previews for tool results, ✻ markers for
thinking — so that I can follow what a worker is doing at a glance instead of decoding
`→ Tool(key=repr, …)` dumps. Replaces the `=== Turn N ===` / `→` / `←` rendering in
`drain_cycle/watch_format.py`; the existing message-id turn dedup stays (the stream emits one
event per content block, all carrying the same `message.id`).

Rendering spec:

- `text` block → blank line, then `⏺ <text>`, continuation lines indented two spaces; assistant
  prose is the payload and is **never truncated**.
- `thinking` block → `✻ <first line, truncated to pane width>` — one line per block, never the
  full body.
- `tool_use` block → blank line, then `⏺ Name(arg)`, whole line truncated to pane width. Display
  name cleans MCP prefixes: `mcp__linear__save_issue` → `linear:save_issue`. The one meaningful
  argument per tool: Bash → first line of `command`; Read/Edit/Write/MultiEdit/NotebookEdit →
  `file_path` as relpath to cwd when it doesn't start with `..` (the pane's cwd is the issue
  worktree, set via tmux `-c`), else basename; Grep/Glob → `pattern`; Task → `description`;
  WebFetch → `url`; WebSearch → `query`; TodoWrite → `"<N> todos"`; any other tool → its first
  string-valued input field, or empty.
- `tool_result` block → `  ⎿ <preview>`: first non-empty line truncated to width, `+N lines`
  suffix when multiline, `(no output)` when empty.
- Unknown block/event types → silently skipped (forward-compat); non-JSON lines echoed verbatim
  (both behaviours exist today and must survive).
- Pane width from `shutil.get_terminal_size` (respects `$COLUMNS`), clamped to a minimum of 40.

## Why

The bet: one meaningful argument per tool call beats the current full-kwargs dump — the operator
scanning a pane needs "which file, which command", not every parameter repr-truncated at 80
chars. Rejected alternative: keeping the kwargs dump and only swapping glyphs — that keeps the
worst readability cost (Edit calls render as a wall of `old_string='…'`) for none of the gain.
Plain-text-first is deliberate: this PR is reviewable as pure string output, and it unblocks the
colour layer (N03) and the status lines (N04) as small follow-on diffs.

## Completion

- **Done when:** each block type renders per the spec above, asserted by unit tests over a fixture
  stream (prose bullet, per-tool args incl. MCP name cleanup and multi-line Bash, ⎿ preview
  variants, ✻ thinking line).
- **Done when:** repeated events carrying the same `message.id` produce exactly one turn boundary
  (dedup pin preserved from the existing suite).
- **Done when:** with `COLUMNS=40`, tool lines and previews truncate with an ellipsis while a long
  `text` block renders in full.

## Assumptions

- The pane's working directory is the issue worktree (tmux `split-window -c <worktree>`), so relpath-to-cwd is the right short form for file paths. *(verified — `_open_watch_pane` passes `-c str(cwd)`)*
- `shutil.get_terminal_size` honours `$COLUMNS`, so tests can pin width without a pty. *(verified — stdlib-documented behaviour)*
- Assistant events repeat per content block with the same `message.id`, so per-block rendering plus id-dedup yields one header per logical turn. *(verified — existing formatter docstring and passing dedup test)*

## Key Risks

- **Risk:** glyphs (⏺ ⎿ ✻) render as mojibake in panes without UTF-8 locale.
  *Falsifier:* the operator's tmux already renders the worker's own UTF-8 output (and this plan's
  glyphs) correctly in the capture-pane replay on N05 — if that replay shows clean glyphs, the
  locale concern is not real for this single-user setup.
- **Risk:** the per-tool argument table goes stale as new tools/MCP servers appear, rendering
  empty `Name()` calls.
  *Mitigation:* the fallback branch (first string-valued input field) means an unknown tool still
  shows something meaningful; the table only upgrades known tools.

## Tasks

- [ ] `skeleton` — Replace the ===/→/← rendering with ⏺/⎿/✻ glyph output end-to-end, turn dedup preserved (golden-fixture test updated as part of the slice) · Done when: the fixture stream renders as the new transcript form and the same-id dedup test still passes · Model: Balanced · risk reversible · review standard · axes RC·SC·HS·SR·OR = M·M·L·L·L
- [ ] Per-tool meaningful-argument extraction with MCP display-name cleanup and first-string-field fallback · Done when: Bash/Read/Edit/Grep/Task/WebFetch/TodoWrite and an `mcp__server__tool` fixture each show the specified argument · Model: Fast · risk reversible · review standard · axes RC·SC·HS·SR·OR = L·M·L·L·L
- [ ] Width truncation clamped ≥40 via terminal size, prose exempt · Done when: under `COLUMNS=40` tool lines and previews carry an ellipsis while a long text block is untruncated · Model: Fast · risk reversible · review standard · axes RC·SC·HS·SR·OR = L·L·L·L·L
