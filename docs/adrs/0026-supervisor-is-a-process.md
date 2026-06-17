# ADR 0026: The supervisor stays a process executing a planned unit; it is not a Claude skill

**Date:** 2026-06-15
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §22

Two coupled decisions about the supervisor's form and scope.

**Scope — a planned unit, not specifically a cycle.** The supervisor executes a *planned unit of work*: a Linear cycle today, a whole project later. The execution atom is unchanged (one issue, a worker per phase, ADR 0024); a cycle and a project differ only as containers with a hierarchy over them (ADR 0025). "Drain a cycle" becomes one entry point rather than the definition. Project execution is out of scope now, but it is a later container on the same machinery, not a redesign — so vision and architecture are written in the generic terms ([`architecture.html`](../architecture.html) §12). The tool keeps the name `drain-cycle`.

**Form — a process with a CLI front-door, not a `/execute-cycle` skill.** It is tempting to encapsulate the supervisor itself as a Claude skill (`/execute-cycle`, `/execute-project`) for one-command ergonomics. Rejected: a skill runs *inside* a Claude session, which makes the supervisor Claude-shaped and collapses the artifact boundary ([architecture.html](../architecture.html) §5) that lets the worker be any vendor. The supervisor's whole value is being content-blind and vendor-agnostic; a skill cannot be that. The one-command ergonomics come instead from a thin CLI front-door (`drain-cycle run <unit>`), while Layer 2 stays skills.

**Why this matters now.** The resident control plane ([architecture.html](../architecture.html) §10) is a long-lived process with an API — that only makes sense as a process, reinforcing the form decision. A control plane implemented as a Claude skill could not be the daemon that spawns and steers vendor-agnostic workers.

**Alternatives considered.**

- */execute-cycle as the primary entry point.* Rejected per above — collapses the vendor-agnostic boundary.
- *Hybrid: a `/execute-cycle` skill that shells out to the process for interactive use.* Not adopted now, but not foreclosed — it is a thin convenience wrapper over `drain-cycle run`, addable later if the keyboard ergonomics warrant it, without moving any supervision logic into the skill.
