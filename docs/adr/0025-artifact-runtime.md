# ADR 0025: Artifact runtime — flexible cognition, composable evidence, deterministic actions

- Status: Accepted
- Date: 2025-09-30
- Supersedes (in part): 0023 open-world cognition, 0024 LLM-first cognition

## Context

The v0.2 LLM-first runtime (`pi/v0.2-llm-first-runtime @ 7c3b6bd`) was independently
reviewed and returned `GENERALIZATION_AUDIT_BLOCKED`. The failures were architectural,
not local bugs: unrelated evidence could make an objective `COMPLETE`; a requested date
window was replaced by season data; team population was inferred from city names; explicit
unsupported constraints were silently dropped or defaulted; the deterministic fallback
contained phrase-specific behavior; a web snippet counted as grounded evidence; local
analytics was a narrow predefined-metric lookup; and the conversation state was raw text.

The common root cause was that the runtime center was a *semantic candidate* / a
`Requirement -> Tool` step with no first-class notion of scope, references, artifact
composition, or coverage. Every fix would have been a local patch to that abstraction.

## Decision

The runtime center becomes a **Goal → Need graph → Planner → Tool → Artifact →
References → re-plan → Sufficiency judge → Response** loop.

Three principles:

1. **Fixed envelope + flexible payload + explicit references.** Cross-component messages
   share a stable envelope (`MessageEnvelope`, `Artifact`, `Need`, `Claim`,
   `ToolRequest`) whose semantic payload is free-form and non-executable. Information is
   carried by reference to originals rather than repeated lossy summaries.
2. **Composable evidence.** Every tool output is an `Artifact` with reusable `exports`.
   Any export can become any accepting tool's input by reference. Tool direction is never
   hard-coded.
3. **Deterministic actions.** Open cognition never implies open actions. Local analytics
   is described by a bounded **Safe Analytical IR**, validated against a trusted
   **SchemaCatalog** and compiled by a deterministic compiler into SQL that still passes
   the read-only AST guard.

## Consequences

- `requested_scope` and `actual_scope` are first-class on Needs and Artifacts. A scope
  difference is an explicit coverage gap; completion depends on coverage, not evidence
  existence. In particular, evidence aggregated over a *broader* window than requested is
  not evidence for the requested window.
- `MetricRegistry` no longer bounds what may be calculated; it documents canonical
  metrics. Ad-hoc derived analyses are built from catalog fields through the IR.
- Shared Knowledge gains a real governance boundary: runtime discovery creates
  `CandidateKnowledge`; only an administrator promotes it to ACTIVE.
- The legacy `BaseballAgent`, `DeterministicCognition`, city-based `select_team` and the
  requirement pipeline remain as a deprecated path (`--legacy` / `--demo` / `--empty`)
  while the artifact runtime is the default. The v0.2 audit reproductions stay runnable
  and now show the failure classes addressed.
- Production behavior no longer branches on known dogfooding literals; a general temporal
  parser and a trusted SchemaCatalog replace the phrase-to-analysis fallback.

## Security

The refactor does not weaken `baseball_readonly`, read-only transactions, the SQL AST
guard, schema validation, DuckDB filesystem sandboxing, SSRF restrictions, credential
handling, tool budgets, or artifact-before-reference ordering. The Safe Analytical IR
never accepts arbitrary SQL fragments, Python expressions, or unvalidated identifiers.

See `docs/artifact-runtime.md` for the full architecture.
