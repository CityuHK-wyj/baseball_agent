# ADR 0009 — Multi-Turn Goal Lifecycle

Status: accepted

## Decision
Every Goal, Need, Attempt and ToolRequest has explicit ownership. Turn classification:
`INDEPENDENT_GOAL`, `FOLLOW_UP`, `CLARIFICATION_ANSWER`, `CONSTRAINT_REVISION`.
A follow-up inherits context (entities, scope selections) but starts a fresh Goal/
requirements set; it does not inherit the previous turn's unresolved requirements.
A clarification answer resumes the Goal that produced the question.

## Consequences
- Requirements from an old question cannot contaminate a new one.
- "MLB" answering "which league?" resumes the full pending question rather than becoming
  a standalone web query.
