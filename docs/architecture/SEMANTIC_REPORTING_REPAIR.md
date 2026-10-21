# Requirement-local reporting and epistemic intent repair

## Scope

This is a repair of two semantic boundaries, not a new agent graph, naming migration,
freeze authorization or replacement of the four governed repair boundaries. See
[ADR 0003](../adr/0003-user-requirement-vs-planner-need.md),
[ADR 0016](../adr/0016-semantic-requirement-completion.md),
[ADR 0025](../adr/0025-verification-responsibility-boundary.md) and
[the existing hard-boundary design](FREEZE_REPAIR_BOUNDARIES.md).

## Original observations are not computation dependencies

An `OperationContract` requests a computed output. Its operands are internal evidence
requirements, not implicit instructions to publish each original statistic. Conversely,
a request for original statistics cannot be discharged by a mathematically correct
computed Claim.

`AnalysisRequest.reporting_contract` proposes the original scalar observations the user
actually requested. `RequirementBuilder` transfers it to the user Requirement. It uses
the existing finite `OperandScope` vocabulary, not a second scope language. Each cell
refines that owner into one mandatory VALUE proposition with a `reporting_index`.
An independent operation request retains its own VALUE proposition and ordered contract.

Examples:

| Requested answer | Communicable value slots |
| --- | --- |
| Only the ordered difference | One derived result; operands stay internal |
| Each year's original total and the difference | Two original observations and one derived result |
| Original totals without arithmetic | Two original observations; no derived artifact is necessary |

The old whole-query measure scan duplicated typed outputs and promoted operand measures
into mandatory reports. Structured report/operation/forecast owners no longer generate
that extra unscoped MEASURE obligation. Ordinary analysis requests retain their existing
measure path. This is not deletion of the original reporting obligation: the reporting
owner now has independently checkable, scoped VALUE children instead of one vague
GROUNDED slot plus a duplicate unscoped measure.

## Acquisition and approval

`analytics.scalar_inputs` selects a unique input from authoritative Goal contracts.
Planner hints may select work within that set, never replace its scope. Subject IDs must
be independently bound; unsupported measures, absent/ambiguous scopes, incompatible
bindings and additional unresolved constraints block the typed path. Original requests
without finite contracts keep the previous binding and identity-validation order.

A selected scope becomes an independent requested analytical contract. The existing
Safe IR validator, requested/executed comparison, read-only compiler/guard, source
availability and receipt production remain in force. Canonical metric packs may produce
the scalar; otherwise the existing bounded IR builder proposes it. This avoids parsing a
Planner objective or an unrelated compound sibling into the source's semantic contract.

`verification.reporting` inspects exact exported cells, reconstructs receipts and reuses
`OperationAdmission.root` unchanged. It requires accepted database roots, complete scalar
population scope, verified measure columns, exact export/row identity and unique matching
support. It records scope, contract/receipt/result digests, cell identity and value.
Inspection is bounded; exhausting the bound does not select a prefix by collection order.

The existing domain scope resolver checks source contradictions; its unresolved task
wording is not declared UNDERSTOOD. Groundable restrictions/grouping/ordering/qualification
cannot disappear merely because a candidate has a scalar schema. Interpreted unsupported
facets remain mandatory constraints. Typed schema/provenance is not a universal proof of
natural-language equivalence; interpretation fidelity still needs real-model evaluation.

The Judge alone creates the original-value Claims and assigns cell-local coverage.
A sibling cell or derived Claim cannot satisfy another reporting index. Missing source B
leaves B unsupported while retaining independently approved A; a failed computation does
not erase separately approved original observations. Whole-state aggregation and the
controller's immutable source manifest conserve every reporting proposition.

A finite operation's successful exact binding selects execution before legacy
objective-word compatibility heuristics. Unbound/free-Python and explicit untrusted
proposal paths retain their legacy guard. Neither candidate execution nor metadata
creates factual approval: unchanged recursive operation admission remains mandatory.

The public backend projection exposes only approved reporting fields when admission was
cell-local. A Claim for one home-run cell does not bless the artifact's sibling metrics,
SQL, receipts, tool narrative or title. No frontend semantic interpretation is added.

## Requested epistemic role stays on its owner

The first observed loss was in the model proposal itself: evidence requests declared
PREDICTION but analysis requests omitted `evidence_intent`. Copying the sibling's role
would be wrong for a mixed historical-report/forecast question.

Both request kinds now declare their own existing typed `EvidenceIntent`. Interpretation
has a bounded schema-feedback repair (at most two semantic proposals, each with a bounded
output budget). Feedback supplies a missing/invalid-field diagnosis, not a guessed role.
If repair exhausts, the original requests and material uncertainty remain; no confirmed
state default is silently invented. This adds no intent enum, agent or Claim type.

The role is retained through SemanticInterpretation, Requirement, Proposition, Planner payload,
frozen AnswerPackage and Composer payload. PREDICTION is the requested answer's epistemic
role, not a synonym for an executable analytics capability. Historical rows can be
premises but do not by themselves satisfy a prediction. Derived factual outputs cannot
be linked as requested original observations or as proof of a predictive conclusion.
Existing source-role, temporal and assertion gates remain in force.

Judge approves evidence and factual premises. Composer may express qualified analysis
under the existing composition/conformance contract; neither the requested role nor a
Composer inference upgrades an uncertain future outcome to a supported historical fact.

## Deliberate limits

The new report lane supports full-population scalar observations only. Grouped, filtered,
ranked/windowed input scopes require the existing analytical path or a future explicit
scope extension; there is no permissive scalar fallback. Additional unresolved global
constraints are conservative blockers when their independence is unproved. Ambiguous
multiple source cells are not selected by order. Arbitrary novel-language entailment,
all possible compound date scopes and general coreference are not claimed solved.

Bounded/pinned evidence transport, whole-state conservation/fallback, actual HTTP retry
telemetry and free-Python non-authority retain their existing contracts.
