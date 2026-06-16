# ADR 0007: Fresh worktree per issue, not a shared workspace

**Date:** 2026-05-22
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §3

Each issue gets `.worktrees/<issue-identifier>/` branched off `main`, used once, then either removed (on Done) or preserved (on halt).

**Alternative considered.** Shared workspace where all issues run in the target repo's main checkout in sequence. Simpler, faster, no worktree plumbing.

**Why worktree-per-issue.** Issues drift. An agent that misunderstands its task can leave the workspace in a broken state — half-applied edits, uncommitted files, branch in the wrong place — that contaminates every subsequent issue's starting point. The worktree gives each issue a clean, identical starting point regardless of what the previous one did, and preservation-on-halt (US-B) means inspectable debug state. The cost is filesystem space and a few seconds of branch setup per issue. Cheap.
