# Web Research v2 — M2 functional addendum

Status: **IMPLEMENTED — internal verification only (not an independent acceptance).**
Baseline: M1 implementation HEAD `3adc40d08d0d1f4bae971ef76db09b8314cb3a18`, plus the bounded
M1-T3 measurement repair (`docs/audit/WEB_RESEARCH_V2_M1_BENCHMARK_REPAIR.md`).
Authority: `docs/SPEC.md` §13, ADRs 0026–0030. This addendum does not reopen the overall
architecture; it records the M2 functionality that now sits behind the unchanged
`web_research` capability.

## 1. Research language policy (M2-C)

`app/web/language.py` separates `user_language`, `primary_research_language`,
`source_language` and `response_language`. General MLB research is **English-first**: the
primary search language is English regardless of the user's input language, because the
discoverable MLB corpus is predominantly English. This is **English-first, not English-only**.

Context-aware expansion fires only on one of four documented triggers (Goal asks for a
regional/community perspective; the likely primary source originates in another language; a
known locale remains unsupported after English research; the target is inherently
language/community scoped such as slang, memes, nicknames or fan discourse). Languages are
derived from markers and known locales, not a fixed baseball list.

`LanguageAwareAliases` supports `canonical_name` + `aliases_by_language`. Aliases shape queries
and are never Evidence. `RetrievedDocument.body_text` and `EvidenceSpan.text` remain in the
original source language (`body_text[start:end] == span.text`); a translation may only be
derived data linked to the original span. `language_policy` and `query_plan` are additive
`WEB_EVIDENCE` metadata. The Result Composer already answers in the user's language
(`semantic.language`), which the M2 integration tests assert through the policy's
`response_language`.

## 2. Query planning (M2-2)

`app/web/query_plan.py` builds a deterministic `ResearchQueryPlan` whose facets map to
mandatory propositions and their required information/scope. Facets are ordered
specific-relationship first (`INTERVIEW`, `FRESHNESS`, `METRIC`, `EVENT`, `RELATION`,
`MISSING`) and the broad **ENTITY** facet is always last, so a generic `Major League Baseball`
query can no longer consume the fetch budget before the relationship query. The bounded LLM
refinement may only append variants; it cannot reorder the deterministic facets. `facets_missing`
records mandatory propositions with no facet.

## 3. Relationship-aware ranking (M2-3)

`app/web/ranking.py` scores each hit on subject/alias coverage, required-information coverage,
time/population scope, intent, `trust_weight` and freshness, minus discovery-only and
duplicate-domain penalties. Authority is bounded below the relationship terms
(`WEIGHTS["trust"] < WEIGHTS["required"] < …`), implementing **relationship relevance > generic
source prestige**: an authoritative but irrelevant page cannot outrank a directly relevant
credible source. Ordering is stable on `(score, url, title)` so discovery/completion order
cannot change the fetched set.

## 4. Proposition-aware span selection and candidate admission (M2-1, M2-4)

`app/web/span_selector.py` selects passages by subject, required relationship, scope, causal /
effect markers, section context and prose quality. It:

- penalises and excludes document titles/headings;
- excludes citation/navigation/metadata fragments unless they actually carry a requested
  relationship token;
- prefers prose over fragments whenever any prose candidate exists;
- merges bounded adjacent-sentence windows for coreference;
- preserves exactly `body_text[start:end] == span.text`.

Subject tokens and subject acronyms are removed from the relationship-token set, so a
subject-only citation fragment can never pass candidate admission. `web_research._verify`
applies `support_eligible` before the unchanged verifier and orders the bounded LLM
verification budget by passage score. **`verify_span`, the Judge, consistency and propositions
are byte-unchanged.**

## 5. Gap assessment (M2-6)

`app/web/gap_assessment.py` classifies the first material failure of an unresolved task as
`QUERY_FAILURE`, `RANKING_FAILURE`, `FETCH_FAILURE`, `SPAN_FAILURE`, `VERIFICATION_FAILURE` or
`SOURCE_COVERAGE_FAILURE` (or `UNKNOWN`/`NONE`). `tools/assess_research_gaps.py` runs it over a
benchmark report. It is diagnostic only and never fabricates a category from ambiguous data.

## 6. What M2 deliberately did not do

No cache, no budget/deadline enforcement, no parallelism, no authenticated broad-search
provider, no structured adapter family, no Planner/Judge/Composer/verifier redesign. Those
remain M3/M4 or explicitly out of scope.

## 7. M2 architecture correction: RelationSemantics ownership

The earlier proposal of a parallel `VerificationIntent` subsystem is withdrawn. The canonical
model is:

```
UserRequirement
├── original_intent        (immutable user request)
├── statement / scope / mandatory metadata
├── relation_semantics     (normalized machine-usable interpretation)
└── AnswerProposition(s)   (atomic answer obligations)
```

- `RelationSemantics` (`app/contracts/models.py`) belongs to the Requirement semantic
  contract, not to Web Research.
- `app/semantic/relations.py` is the single definition of the CAUSE/EFFECT/QUOTE/STATUS/
  FRESHNESS/VALUE/COMPARISON/AGGREGATION/EXPLANATION vocabulary that was previously
  duplicated across `query_plan.py`, `ranking.py`, `span_selector.py`, candidate admission and
  verifier preparation.
- `original_intent` is populated once by `RequirementBuilder` and is never overwritten by the
  normalized `statement`/`relation_semantics` interpretation.
- `AnswerProposition` carries the same semantics, so one meaning reaches every
  capability-specific consumer.

`relation_kind` is a routing signal, not a hard tool rule: VALUE/AGGREGATION/COMPARISON lean
analytical, CAUSE/EXPLANATION/QUOTE lean research, STATUS leans a current authoritative
source. Each capability keeps its own evidence/proof mechanism; database-derived analytical
facts are not routed through the Web EvidenceVerifier for uniformity. Verifier preparation
adds relational cues to the required-information match only for CAUSE/EFFECT/QUOTE/
COMPARISON/EXPLANATION; STATUS/FRESHNESS stay routing-only so broad currentness vocabulary
never becomes support. `verify_span`, the Judge, the Composer and the consistency checker are
unchanged. `shared_knowledge` may assist interpretation across layers but cannot satisfy a
factual Requirement or bypass Artifact/Judge governance.

See `docs/audit/WEB_RESEARCH_V2_M2_CLOSURE.md` for the A–E diagnosis and the closure results.

## 8. Answer Proposition decomposition (M2 completion)

`RelationSemantics` is multi-label (`relation_kinds`); it stays open and non-prescriptive. A
Requirement may produce several atomic AnswerPropositions, each independently evidence-testable.
`app/semantic/decomposition.py` performs the decomposition: it strips task language, removes the
subject and evidence-carrier nouns from the required-information slot, splits a compound
CAUSE+EFFECT obligation, and validates every candidate against the domain schema. The LLM is
consulted only for genuinely compound natural-language wording and its output is rejected (with
a deterministic fallback) when it invents numbers/identifiers, uses a non-provided subject,
or emits malformed relation semantics. The Proposition Builder never selects tools.

`span_selector.py` recovers evidence context by missing slot (subject, relation, time/population
scope) rather than a fixed sentence count, bounded by the character cap and exact source offsets.

Implementation report: `docs/audit/WEB_RESEARCH_V2_M2_FUNCTIONAL_COMPLETION.md`.

## 9. Temporal scope and source-coverage escalation (M2.5)

Relative time is resolved once at Requirement normalization (`app/semantic/temporal.py`) into a
`TemporalScope` on the Requirement and Goal, anchored at Goal creation. The original wording is
preserved; defaults are central and per information class; unanchorable expressions stay
`UNRESOLVED`. The resolved window is rendered into the web artifact `actual_scope.date_range` so
the unchanged Judge can satisfy the `time` scope channel. Temporal resolution never chooses a
capability.

When direct sources cannot discover the requested relation, `web_research` escalates to a real
keyless broad-search surface (Brave/Bing public HTML, discovery only) under a bounded budget,
pre-fetch when direct candidates are subject/relation blind and post-verification when no
mandatory proposition is supported. Broad hits remain candidates that must be fetched and
verified; provider failures are typed; English-first and context-specific multilingual search
are unchanged.

Implementation report: `docs/audit/WEB_RESEARCH_V2_M25_FUNCTIONAL_COVERAGE.md`.

## 10. Answer composition contract (M2.6)

The Judge → Composer boundary is an explicit answer navigation contract (`AnswerPackage` in
`app/response/answer_contract.py`): approved factual text plus references to the accepted
claims, findings, spans and documents, the requirement coverage, and the unresolved
requirements. The Composer may read the approved evidence graph and bounded surrounding
context, but may only assert Judge-approved facts. Every satisfied mandatory prose proposition
must be expressed for COMPLETE and LIMITed Goals; synthesis, merging and paraphrase are allowed,
while retraction, omission, inverted numbers and swapped metric values are not. The Evidence
Verifier, evidence lineage and Judge sufficiency remain protected.

Implementation report: `docs/audit/WEB_RESEARCH_V2_M26_ANSWER_COMPOSITION.md`.
