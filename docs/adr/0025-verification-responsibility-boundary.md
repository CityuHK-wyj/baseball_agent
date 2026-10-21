# ADR 0025 — Verification Responsibility Boundary and Phase 5B Closure

Status: accepted (Phase 5B closure, 2026-09-27)

## Context

The independent Phase 5B revalidation (`docs/audit/PHASE5B_INCREMENTAL_REVALIDATION.md`)
confirmed that the runtime's *responsibilities* were not aligned with its *authorities*:

- a search-result title could manufacture a `SUPPORTED` finding;
- an LLM span verifier could overwrite a deterministic year/population conflict;
- `EvidenceFinding.span_id` did not resolve to persisted `EvidenceSpan`s;
- the post-composition consistency check both missed retractions and produced false
  downgrades, and its LLM adjudication could unilaterally change the status;
- diagnostics leaked secrets embedded in strings and erased legitimate token telemetry.

The project must keep exactly one runtime flow:

```text
User Message -> Semantic -> Goal/Requirements -> Planner -> Tool Execution
             -> Artifacts -> Judge -> Answer
```

Evidence verification is an internal capability of evidence-producing tools, not an
additional top-level agent or a parallel orchestration graph.

## Decision

### Responsibility boundary (one flow, three post-tool questions)

| Component | Question | Owns | Must not |
|---|---|---|---|
| Evidence Verifier (inside `web_research`) | What does *this evidence* actually establish? | admissibility, provenance, hard scope constraints, per-span `EvidenceFinding`, local gaps | overall GoalStatus, replanning, final answer |
| Judge | Do the verified artifacts collectively satisfy the user's Requirements? | Requirement coverage, GoalStatus, supported partial results, remaining gaps, answer material | a second semantic re-reading of every article; tool success / artifact count as proof |
| Composer | How is the verified answer communicated? | rendering supported facts and qualified inferences, sources, limitations, GoalStatus | re-evaluating completeness, promoting unsupported claims, contradicting accepted verification |

`AnswerProposition` remains a derived refinement of `UserRequirement` (ADR 0021). No
`Evidence Agent`, `Judge Agent` or `Composer Agent` orchestration loops are introduced.

### Evidence admission (ADR 0022 amendment)

`EvidenceSpan` now declares `source_type` and `retrieval_status`. Only a
`DOCUMENT_BODY` span of an `OK` fetched document with consistent offsets is *admissible*.
A search title or snippet may still drive discovery relevance, but it can never satisfy
the subject, scope or required-information conditions of a finding. Discovery relevance
and factual support are separate decisions.

### Deterministic hard gate (ADR 0022 amendment)

`verify_span` runs in a fixed order:

1. inspect provenance/admissibility;
2. evaluate deterministic subject/time/population constraints on the passage;
3. classify each as `MATCH` / `MISMATCH` / `UNKNOWN` / `NOT_REQUESTED`;
4. if admissible and no hard gate is set, optionally ask the LLM verifier to refine;
5. persist the final finding.

A known conflicting year or population, a missing subject, or an unresolved requested
scope freezes the decision: the LLM is **not called** (`merge_verification`), and it can
never upgrade the finding to `SUPPORTED`. `UNKNOWN` (missing metadata) and `MISMATCH` (a
known conflict) stay distinct fields. `EvidenceFinding.deterministic_status` and
`llm_status` record both decisions when a semantic refinement is actually allowed.

### Evidence lineage (ADR 0022 amendment)

`web_research` persists every span with a stable id and its `document_id`, validates
`document_text[start:end] == span.text` at construction, and stores the collection in
`Artifact.structured_data["evidence_spans"]`. Every `EvidenceFinding.span_id` must resolve
to a persisted span whose document is an `OK` retrieved page with consistent offsets.
The Judge drops findings that do not resolve and never reconstructs a fictional span.

### Consistency (ADR 0023 amendment)

Post-composition consistency is a **structured comparison** between the rendered answer
and the accepted claims attached to each mandatory satisfied proposition. It does not
re-read evidence and does not call an LLM. When a mandatory result is missing, the runtime
prefers a bounded, deterministic repair (appending the already-verified claims). Only if
the repair still cannot express the result is the run downgraded. Missing *optional*
material never downgrades `COMPLETE`, and an unrelated limitation does not count as a
retraction of an expressed result.

### Diagnostics (ADR 0024 amendment)

Redaction is field-aware and value-aware: credential fields (exact names and credential
suffixes) are masked; telemetry fields such as `subject_tokens`, `required_tokens` and
`matched_tokens` are preserved; credentials embedded in URLs, `Authorization` headers,
bearer tokens and cookies are scrubbed. The diagnostics directory/file are created
`0700`/`0600`. `.runtime/` and `.scratch/` remain Git-ignored.

## Consequences

Final repair amendment (2026-09-27): SPEC §12.7 defines exact body replay, span and
obligation signatures, metric ownership, mandatory VALUE expression units, and minimized
diagnostics with measured provider usage. The Judge remains the final status authority,
including `reconcile_expression` after bounded expression repair; consistency does not
decide evidence sufficiency. Default diagnostics no longer preserve free-text evidence;
explicit restricted research mode and its retention limits are documented in §12.7.

Bounded closure amendment (F1–F4): SPEC §12.7 requires value and scope on one compatible
metric predicate and freezes UNKNOWN attribution. For COMPLETE mandatory results,
Composer renders accepted Claims within a fixed output budget; consistency confirms
complete expression units/structured values and replaces unconfirmed prose with accepted
results. It preserves accepted negative facts and never infers evidence sufficiency.
The research-content diagnostic option is metadata-only: arbitrary body capture is
disabled and final writes sanitize mutated records. Existing runtime evidence artifacts,
Judge coverage/status authority and provider usage collection are preserved. This is a
bounded contract, not a claim to solve arbitrary language equivalence or Web retrieval.

- A title or snippet can never ground a claim.
- A deterministic conflict can never be talked away by a fluent model.
- The evidence chain is inspectable end to end: finding -> span -> document -> source.
- `SUCCESS`/`COMPLETE` cannot be manufactured by artifact count, tool success or a
  confident-sounding answer.
- The consistency check is a narrow safety boundary, not a third Judge.
- The semantic verifier is only consulted where interpretation is actually needed,
  removing redundant LLM calls and latency on hard-conflict pages.
- Retrieval coverage remains a separate Web Research v2 capability; a missing page still
  yields a typed, honest `LIMITED` result rather than a fabricated answer.
