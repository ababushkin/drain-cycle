# Swimlanes worker-stream fixtures

Two captured `exec:review` stream shapes, one per worker family, plus the
residual OQ-1 evidence they encode. `tests/swimlanes/test_marker_contract.py`
feeds both and proves persona depth comes through the `_active` marker on either
worker (design-doc NFR-6).

## `claude-review-stream.jsonl`

A Claude-Code `exec:review` dispatch: a `Skill` `tool_use` block names the step
(`input.skill == "exec:review"`, machine-readable), followed by the `Agent`
fan-out — one block per persona. Each `Agent` input is `{description, prompt}`
with **no persona field**; the persona name lives only in free-text prompt
content. So the stream yields the *step* but not the *persona*.

## `codex-review-stream.jsonl`

A codex `exec:review` dispatch run inline-sequentially: the personas are loaded
and applied in-line (a `Read` of the persona file, then text), with **no
`Skill` or `Agent` tool boundary**. The stream yields neither the step nor the
persona.

## Residual OQ-1 evidence

OQ-1 cleared step-depth from the stream (the `Skill` block carries
`input.skill`). The residual question was whether a *real* persona dispatch
exposes the persona in the stream. These fixtures answer it: it does not — on
Claude the `Agent` input has no persona field, and on codex there is no tool
boundary at all. Persona-from-stream is therefore best-effort on Claude and
impossible on codex, which is why the contract puts persona identity on the
pack-written `_active` marker (ADR 0032) rather than parsing it from the stream.
