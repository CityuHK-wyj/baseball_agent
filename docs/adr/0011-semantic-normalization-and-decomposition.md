# Semantic normalization, requirement decomposition and adequacy rules

Status: accepted

## Context

D023/D024/D026/D034/D038/D039/D045 and posts 01–09 require a Normalization layer before
Plan, an immutable Initial Requirement baseline produced by a Requirement Decomposer,
user-facing clarification for meaning ambiguity, canonical entities, and a split between
qualification and sample adequacy with authoritative league progress. None of this
existed; `app/semantic/*` were placeholders and the Planner was fed raw objectives.

## Decision

- Constraint origin/authority: `ConstraintOrigin`
  (USER_EXPLICIT / USER_CONFIRMED / CONTEXT_INFERRED / SYSTEM_INFERRED / SYSTEM_DEFAULT)
  and `ConstraintAuthority` (SYSTEM_POLICY > USER_CONSTRAINT > USER_PREFERENCE >
  INFERRED_DEFAULT). `_Constraint` fills `authority` from `origin` when absent, so the
  existing contract stays backward compatible.
- `app/semantic/constraints.py`: `normalize_constraints` keeps the highest authority per
  key and preserves equal-authority conflicts (e.g. a numeric range).
- Entities: `CanonicalEntity`/`EntityCandidate`/`EntityResolution` plus
  `EntityDictionary` + `EntityResolver`. A display name is never an identity; the
  resolver commits only when one candidate is unambiguously strongest, otherwise it
  proposes a `ClarificationRequest` with at most four options and a recommendation.
- Clarification: `ClarificationRequest`/`ClarificationOption`/`ClarificationAnswer`.
  Meaning ambiguity is the user's decision (D045); the system may recommend, never
  silently choose.
- Objectives: `AnalysisObjective` gains `entities` and `base_priority`.
  `RuleBasedObjectiveExtractor` maps query cues to INJURY/VALUE/STRATEGY/CONTEXT and
  defaults to PERFORMANCE; an LLM extractor can implement the same Protocol.
- `SemanticNormalizer` composes extraction + entity resolution + constraint
  normalization into a `SemanticResult` (objectives, clarifications, unresolved
  mentions). The Planner never re-parses raw intent (D023).
- Requirement Decomposer: `RequirementDecomposer` Protocol +
  `RuleBasedRequirementDecomposer`, producing semantic-atomic `INITIAL`
  `ArtifactRequirement`s per objective type. It selects no tool and carries no source
  field; it propagates objective entities and constraints.
- Adequacy: `QualificationRule` (ALL_PLAYERS/MLB_QUALIFIED/CUSTOM),
  `SampleAdequacyRule` (min sample + unit) and `LeagueStateSnapshot` (authoritative
  games played vs scheduled, local coverage end). `app/assessment/adequacy.py` turns
  these into soft signals and a user-relevant coverage limitation.
- State enrichment: `RequirementState` gains artifact/limitation/unresolved/blocking
  refs and `recoverable`; `ObjectiveState` gains limitations, optional gaps and
  replan count. Both remain projections, never definitions.

## Alternatives

- Let the Planner interpret intent: rejected by D023.
- Silently pick the most famous entity on ambiguity: rejected by D004/D045.
- One requirement per metric: rejected; the blog requires semantic atomicity.
- A single `minimum_sample_size`: rejected by D038.

## Consequences

- The semantic layer is deterministic and testable with no model or database; LLM
  implementations remain a Protocol swap.
- The requirement `min_row_count` remains as a legacy shortcut; `sample_adequacy_rule`
  takes precedence when present.
- The decomposer is intentionally small; richer decompositions belong behind the same
  Protocol and must not drop user requirements.
- Entity dictionary and league snapshot are in-memory inputs; their persistent/external
  sources remain open (O007).
