# `.drain-handoff.json` — schema v2

Contract reference for the drain-cycle supervisor's exit record. Authored alongside
`docs/adrs/0002-thin-supervisor-contract.md`. The supervisor reads this file at run-end to
grade, append the run-log entry, and (on a non-Done exit) name the halt path.

## Scope

`.drain-handoff.json` is the **exit record**: a worker writes it (and the supervisor stamps a
final field on it) once per drained issue. It is not the in-flight carrier between pack skills —
that is `pickup-envelope.json`, owned by the pack's execution-workflow doc (A/N04). The two files
do not overlap; the ADR's writer/reader table is the single source for which field lives where.

## Schema (JSON Schema 2020-12)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://drain-cycle/docs/adrs/references/drain-handoff-schema-v2.json",
  "title": "drain-handoff",
  "type": "object",
  "required": ["exit_code", "final_linear_state", "halt_reason"],
  "additionalProperties": false,
  "properties": {
    "pr_urls": {
      "type": "array",
      "items": {"type": "string", "format": "uri"},
      "description": "Submitted PR URLs. Required when final_linear_state == 'Done'."
    },
    "final_linear_state": {
      "type": "string",
      "description": "Linear workflow-state name the supervisor records on the issue at exit. 'Done' implies a fully drained run; any other value implies a halt and requires halt_reason."
    },
    "exit_code": {
      "type": "integer",
      "minimum": 0,
      "description": "Worker process exit code. Supervisor-written."
    },
    "outcome_verdict": {
      "type": "object",
      "required": ["result", "failed_ac"],
      "additionalProperties": false,
      "properties": {
        "result": {"enum": ["pass", "fail"]},
        "failed_ac": {
          "type": "array",
          "items": {"type": "string"},
          "description": "Verbatim AC-item identifiers (matching the pickup-envelope's ac_checklist) that did not pass. Empty on pass."
        }
      },
      "description": "Written by exec:verify (via shape:verify-implementation). Absent on halts that occurred before exec:verify ran."
    },
    "prep_verdict": {
      "type": "object",
      "required": ["route", "reasoning"],
      "additionalProperties": false,
      "properties": {
        "route": {"enum": ["auto-merge", "human-review"]},
        "reasoning": {"type": "string", "minLength": 1}
      },
      "description": "Written by shape:pr-prepare (invoked by exec:finish). Absent on halts that occurred before exec:finish ran."
    },
    "halt_reason": {
      "oneOf": [
        {"type": "null"},
        {
          "enum": [
            "worker-exit-1",
            "timeout",
            "repeated-exit-1",
            "verify-fail-noloop",
            "pr-blocked",
            "human-review-requested"
          ]
        }
      ],
      "description": "Required when final_linear_state != 'Done'. Closed set — extending it requires a contract edit (see ADR 0002 § Operability, rollback step 3)."
    }
  },
  "allOf": [
    {
      "if": {"properties": {"final_linear_state": {"const": "Done"}}},
      "then": {
        "required": ["pr_urls", "outcome_verdict", "prep_verdict"],
        "properties": {"halt_reason": {"type": "null"}}
      }
    },
    {
      "if": {"properties": {"final_linear_state": {"not": {"const": "Done"}}}},
      "then": {
        "properties": {"halt_reason": {"type": "string"}}
      }
    }
  ]
}
```

## Fitness-function contract

The run-log build step parses every file under `~/.drain-cycle/runs/*.json` against this schema.
A failure is a halt-blocker: the supervisor reports the offending run and exits non-zero. The
pre-merge check in `drain-cycle` (and the mirror in `agent-skills-shaper`, where the pack skills
that write these fields live) runs the same parse against the fixture corpus under
`docs/adrs/references/drain-handoff-schema-v2-fixtures/` (authored at N03 / N04).

## Writer / reader allocation

See `docs/adrs/0002-thin-supervisor-contract.md` § Decision for the canonical field-by-field
allocation. Do not duplicate it here — one source.

## Extending the halt-reason taxonomy

A new halt path needs:

1. One enum entry added under `halt_reason` here.
2. The writer (supervisor or skill) updated to set it.
3. A fixture under `docs/adrs/references/drain-handoff-schema-v2-fixtures/halt-<code>.json` exercising it.
4. Plan-review on the ADR is not required for taxonomy additions — they are additive and
   reversible. A *removal* requires re-opening the contract.

## Versioning

No `schema_version` field. v1's field set is a strict subset; v1 readers tolerate the v2 fields
they do not know. A future v3 — should one ever be required — supersedes this reference with a
new file path; readers branch on file presence, not on an in-file version field.
