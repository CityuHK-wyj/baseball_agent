# Baseball Agent Design Principles

These are Baseball Agent architecture constraints. They constrain relevant design,
implementation, debugging and review; they do not authorize unrelated feature work.

## Language and execution boundary

### Open-world language, closed-world execution

Natural language has effectively unlimited valid forms. Execution semantics must
remain finite, typed and governed. Expand language coverage through interpretation,
not through endless wording enumeration.

Do not attempt to enumerate every valid user expression with regex alternations,
keyword dictionaries, task-verb lists, multilingual sentence lists, typo lists or
benchmark-specific strings. Finite deterministic rules are appropriate for governed
schema fields, roles, operators, qualification bases, categorical values, game types,
canonical identifiers and validated derivation kinds.

### LLM proposes meaning; deterministic code validates

LLM output is hypothesis generation, not truth or authority:

User language → semantic candidate → context / Shared Knowledge / grounding →
deterministic validation → governed Requirement → execution.

The LLM may propose meaning. It may not directly establish trusted SQL, execution
filters, Measure identity, Requirement authority or factual truth. A valid candidate
schema alone does not prove that the candidate follows from the user's language.

### Interpret → Ground → Clarify

Resolve uncertainty with information, not guessing. Interpret the likely meaning,
use conversation context, consult stable Shared Knowledge where applicable, bind
entities, schema, Measures, temporal scope and population, use existing Evidence,
then re-evaluate ambiguity. Ask the user if material ambiguity remains.

Do not guess materially different meanings merely to avoid clarification. User
clarification is a normal Agent capability. Target the smallest unresolved scope
and let the backend validate the answer and its effect on Requirements.

### Lexical signals are weak hints, not semantic authority

A lexical cue may help segmentation or candidate generation. It must not by itself
prove semantic completeness, Requirement authority, clause UNDERSTOOD, coverage
COMPLETE, semantic irrelevance, Requirement independence or permission to execute.

`Explain that`, `Show his numbers there` and `Report the same split` may look
task-shaped while remaining unresolved. Task verbs and role words are weak hints
unless independently grounded. Trace their actual callers: a predicate described
as a hint may still change which obligations downstream validation sees.

### Do not solve open language with vocabulary accretion

Do not default to failure → add keyword → add regex → rerun benchmark. First ask
whether the failure reveals an inadequate semantic candidate schema, missing
grounding, context resolution or Shared Knowledge, clause-consumption loss,
incorrect authority or missing clarification behavior.

Regex is not inherently wrong. Keep it for genuinely finite syntax and deterministic
formats. Ask: does correctness depend on enumerating arbitrary human wording? If
yes, reconsider the abstraction. A finite list of human task verbs does not make
the underlying language domain closed-world.

### Centralize stable terminology; do not enumerate typos

Stable baseball aliases such as LHP, southpaw, left-handed pitcher and canonical
abbreviations may live in Shared Knowledge, a governed lexicon or domain ontology.
They assist interpretation; they must not grow into an enumeration of user sentences
scattered through business logic.

Do not list `pitcher`, `picher`, `pitchr`, `pitcherr`, and so on. Let the interpreter
recover obvious meaning, then validate the governed concept. An ambiguous typo
remains TEMPORARILY_UNRESOLVED or requires clarification. Never fuzzy-match arbitrary
text directly to the nearest schema field and execute.

## Semantic conservation

Every answer-changing obligation must survive source text → Requirement → analytical
contract → IR → execution → Artifact → answer. No layer may silently delete mandatory
meaning.

### Partial resolution is not full understanding

For `against lefties with two strikes`, resolving `p_throws = L` does not consume
`strikes = 2`. The remaining meaning must stay visible; one resolved fragment does
not make the whole clause UNDERSTOOD.

### Lossless clause consumption

Track, conceptually, original source meaning, consumed semantic content, structured
obligations, ignored content and remaining relevant content. A mandatory clause is
UNDERSTOOD only when every answer-changing meaning is represented in governed
structure or explicitly and safely proven irrelevant.

Otherwise preserve TEMPORARILY_UNRESOLVED, UNSUPPORTED or INVALID as appropriate.
Do not erase short tokens, numbers, handedness markers or CJK fragments because
they complicate residue analysis. Honest incompleteness beats fabricated COMPLETE.

Clause resolution states, Requirement coverage and Goal status belong to different
levels. Preserve the backend's governed enums: TEMPORARILY_UNRESOLVED is not a new
GoalStatus, and a presentation-layer PARTIAL must not be synthesized as factual truth.
Use the appropriate existing incomplete state, such as WAITING_FOR_USER or LIMITED,
rather than inventing COMPLETE.

## Authority

### Provenance is not semantic proof

Provenance answers "Where did this come from?" Semantic proof answers "Does this
meaning actually follow?" Both matter. Parent IDs, a recognized derivation name or
nonempty proof prose do not establish that a child Requirement follows from its
parent. Validate the actual governed semantic transformation.

### Planning metadata cannot launder authority

Planner metadata, Need metadata, Tool proposals and model candidates cannot silently
become authoritative user obligations. Authority ultimately derives from explicit
user language, valid clarification or governed derivation from authoritative input.

## Evidence and execution

### Collected Data != Approved Data

Tool output, retrieved rows and generated analysis are candidates. They become
answerable evidence only after required validation, conformance, lineage and Judge
approval. A query returning rows is insufficient: those rows must match the requested
semantics and be attributable to the Requirement they support.

Composer and frontend present approved evidence; neither creates factual truth or
self-certifies sufficiency.

### Requested and executed contracts are distinct

Internally consistent generated IR or SQL does not prove the execution matched the
request. Compare the actual execution against the authoritative current Requirements.
Keep requested contracts, execution receipts and conformance independently inspectable.
Check entity, role, filters, temporal scope, population, qualification, grouping,
ordering and limit as applicable, including omitted and extra restrictions.

### Correctness before convenience, with utility measured

Never weaken semantic conservation, authority, conformance, evidence lineage,
credential safety or database/execution safety to improve execution rate or UI
smoothness. Also avoid becoming "safe" only by rejecting valid language.

Track safe execution, correct interpretation, unnecessary rejection, clarification
rate, answer quality and numerical correctness. The critical invariant is
`wrong semantic COMPLETE = 0`. A finite passing sample is evidence, not proof of
universal correctness. Safety and utility must both be measured.

## Requirement and Goal responsibilities

| Concept | Responsibility |
| --- | --- |
| Requirement | What the user needs and what must be satisfied. |
| RelationSemantics | The relevant relationships and their semantics. |
| Proposition | An evidence-testable claim. |
| Planner | How to satisfy Requirements through work and dependencies. |
| Artifact | Evidence/results, scope, provenance and lineage. |
| Judge | Evidence sufficiency and coverage against Requirements. |

These are separate concepts. A Tool call or Artifact's existence is not satisfaction
of a Requirement, and Planner work items do not redefine user completion semantics.

### Approval is Requirement-local; completion is Goal-level

Where semantic independence is proven, a correctly supported Requirement retains
its approved Artifact when another independent Requirement remains unresolved.
Dependency, not Goal membership alone, determines blocking. The Goal remains
incomplete until all mandatory obligations satisfy the backend completion rules.

For independent tasks A and B, retain supported A while resolving B. If A depends
on B, block A. For one compound Requirement, `AVG against lefties with two strikes`,
unresolved `two strikes` prevents presenting `AVG against lefties` as satisfying
the original Requirement. Do not expose a partial fact whose semantics depend on
an unresolved obligation.

Trace partition membership, shared constraints and Proposition dependencies as well
as receipt validation. A smaller contract can validate execution accurately while
still being the wrong contract if independence was incorrectly inferred. Do not
claim this invariant is implemented unless tests demonstrate it.

## Unresolved semantics and clarification

TEMPORARILY_UNRESOLVED remains actionable through Planner rounds, replans, Tool
attempts and clarification pauses. Do not silently rebuild away unresolved meaning
or resolution history.

Retry only when materially new information exists, such as user clarification,
newly retrieved Shared Knowledge, new entity binding Evidence or changed Tool
availability. Avoid duplicate attempts without new information and repeatedly
asking about already resolved ambiguity.

Retain evidence and Artifacts for supported independent Requirements, continue
resolving the others, and keep the overall Goal incomplete. Clarification should
invalidate only affected dependent results.

## Shared Knowledge boundary

Shared Knowledge may assist terminology, metric definitions, stable domain concepts,
interpretation, routing and query construction. It must not silently satisfy mutable
factual Requirements. Mutable facts still pass through Artifact → Evidence → Judge.

## Frontend boundary

### Frontend collects intent; backend owns semantics

The frontend may display backend clarification options, collect selections, render
approved Artifacts, show authoritative status and sort/filter approved presentation
data. It must not independently determine entity identity, role, Measure, population,
temporal scope, qualification, Requirement authority or factual truth.

Clarification options are backend-defined and semantically opaque to the UI. UI,
tables, charts and formatting consume approved structured results; presentation
convenience never redefines the factual contract.

### No model-generated executable UI

Models may propose typed presentation intent. Do not directly execute arbitrary
model-generated JavaScript, JSX, HTML or SQL. Render governed Artifact types with
trusted frontend components. SQL, IR, receipts and credentials remain private to
the backend rather than becoming unrestricted presentation payloads.

## Development discipline

### Architecture-first development

The preferred workflow is Architecture → Domain Modeling → ADR → SPEC → Tickets →
TDD → Implementation → Code Review → Independent Acceptance. Small repairs need
the same architectural reasoning without unnecessary ceremony. Existing task
authorization and scope still govern which steps and changes are appropriate.

### Diagnose → repair → test → repeat

During authorized development, diagnose → repair → test → inspect failure → repair →
repeat. Do not stop at a partial implementation with known in-scope failures. A
review-only task reports failures; it does not authorize production repair.

### Tests are evidence, not the semantic specification

A failed example may reveal a missing abstraction. Ask which general mechanism
caused it and test that mechanism. A passing benchmark does not justify a brittle
wording rule. Build abstractions instead of accumulating phrase exceptions.

### Fresh paraphrase and metamorphic evaluation

Language changes require fresh paraphrases, clause reorderings, multilingual and
mixed-language forms, abbreviations, typo mutations, adversarial compound clauses
and metamorphic equivalents. Include both safe valid questions and incorrect or
ambiguous inputs. Production code must not know the evaluation vocabulary.

### Freeze discipline

Do not freeze with known critical development defects. After freeze, use genuinely
unseen evaluation cases, do not patch production to those frozen cases, and record
failures honestly for the next repair milestone.

## Engineering priorities

Semantic correctness, evidence integrity, authority correctness, credential safety
and database/execution safety are hard invariants. Within them prioritize functional
completeness, then answer/evidence quality, then performance optimization.

Prefer risk-based safety. Do not add major complexity for contrived edge cases unless
they expose a real semantic failure mechanism.

## Repository context

Find the repository root from the working checkout, not from the skill directory.
Read its architecture documentation, SPEC and relevant ADRs. In the rebuild these
include `ARCHITECTURE.md`, `docs/SPEC.md` and `docs/adr/`.

For historical examples, look under `docs/audit/` and `docs/architecture/` for the
natural-language hardcoding audit, M2.12R4 implementation report, Requirement-local
partial approval report and frontend interaction foundation; the frontend boundary
decision is recorded under `docs/adr/`. Discover current paths by topic if names
change. These principles are self-contained and do not require any historical
filename. Do not copy entire ADRs or audits into the skill.
