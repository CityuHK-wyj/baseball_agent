# ADR 0002 — Fixed Envelope, Flexible Payload, Explicit References

Status: accepted

## Decision
Inter-component envelopes (`SemanticInterpretation`, `PlannerNeed`, `Artifact`, `ToolOutcome`)
have stable keys, but semantically open content lives in free text/loosely typed fields
plus explicit `Reference` objects. Not every natural-language concept must become an enum
before it can travel through the system.

## Rationale
A "why did he get stronger recently" analysis goal or a "is there an injury/mechanical
adjustment report" evidence request must be preserved even when no metric or DSL matches.

## Consequences
- `structured_hints` are hints, never the sole authority for user intent.
- References (`source_ref`) prevent summary-of-summary information loss.
- Strict typing is applied only at privileged actions (SQL, filesystem, network).
