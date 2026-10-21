# Web Research v2 — M1 Executable Tickets

Status: **APPROVED FOR TDD IMPLEMENTATION — not yet implemented.**
Baseline: Phase 5B pragmatic acceptance, reviewed HEAD `7ad65b2fdc5e0b87355c122a23864ed547a172f3`
Architecture: `docs/architecture/WEB_RESEARCH_V2.md`
ADRs: 0026–0030 (`docs/adr/`)
SPEC: `docs/SPEC.md` §13

This document turns M1-T1 … M1-T7 into executable tickets. M1 is the **only** milestone in
scope now. M2 (query facets, relationship ranking, bounded parallel fetch), M3 (budget/deadline
enforcement, full cache) and M4 (optional broad-search provider, structured adapters) must not
be implemented early.

---

## 0. M1 global constraints

Non-negotiable for every ticket:

1. **Do not modify** the Judge (`app/verification/judge.py`), the Evidence Verifier
   (`app/verification/evidence.py`), the Composer (`app/response/composer.py`), consistency
   (`app/verification/consistency.py`) or propositions (`app/verification/propositions.py`).
2. **No new Planner-visible capability or tool.** `web_research` stays the only research
   capability.
3. **No authenticated provider.** M1 uses only keyless, working sources.
4. **No M2–M4 behavior.** No query-facet generation, no relationship ranking in the production
   selection path, no bounded parallel search/fetch, no cache reads/writes, no budget
   enforcement, no structured adapter family.
5. **Preserve the Phase 5B independent acceptance baseline.** The Phase 5B reports, contracts
   and regression tests remain valid and must pass unchanged.
6. **TDD.** Each ticket's tests are written first and must fail before the implementation.
7. **Additive metadata only.** New keys go into `Artifact.structured_data` / diagnostics; no
   accepted pydantic contract field is removed or repurposed.

M1 exit acceptance (all tickets complete):

- A **real Wikipedia historical question** is retrieved over the network, a page body is
  fetched, offsets replay (`body[start:end] == span.text`), and the existing verifier
  produces a finding. Judge/Verifier/Composer untouched.
- A measured **old-vs-new baseline** on a frozen task set for **Answer Completion, Evidence
  Precision and Latency** (Phase 5B at `7ad65b2` vs the M1 build).
- Full offline suite green; live probe record attached.

---

## M1-T1 — Per-provider latency spans + `research_metrics` (contracts: budget type)

**Goal.** Make research latency, provider usage and budget consumption observable without
changing any answer behavior; declare the `GoalResearchBudget`/`ResearchBudget` types (no
enforcement in M1).

**Inputs.**
- `app/observe/diagnostics.py` (`LatencyTracker`, `DiagnosticRecorder`, redaction rules).
- `app/runtime/engine.py` `_record_diagnostics`.
- `app/capabilities/web_research.py` `_search`, `_retrieve`, `_verify`.
- Diagnostic records already persisted under `.runtime/diagnostics/`.

**Outputs.**
- Latency spans `web:source:<source_id>`, `web:rank` (may be 0 in M1), `web:cache` (0 in M1),
  in addition to the existing `web:search`/`web:fetch`/`web:verify`.
- A `research_metrics` diagnostic record: provider calls (per source), documents fetched,
  fetch outcomes, LLM query/verify call counts, research token usage, and M1 budget counters.
- `app/web/budget.py` declaring `GoalResearchBudget` and `ResearchBudget` dataclasses with
  the fields in SPEC §13.6 / ADR 0028 — **type/contract only; no enforcement, no reset logic
  yet**.
- Additive `Artifact.structured_data["research_metrics"]`.

**Tests (write first).**
- Offline: a web turn writes a `research_metrics` record with the documented schema; unknown
  fields are omitted by the existing recursive policy (no bodies, URLs, prompts, credentials).
- Offline: per-source spans are emitted once per source call; counts reconcile with the
  `research_metrics` provider-call count.
- Regression: existing diagnostics tests (`test_goal_diagnostics_are_written_with_latency`,
  Phase 5B redaction tests) pass.
- Offline: `GoalResearchBudget`/`ResearchBudget` construction and `effective = min(tool,
  remaining)` helper (pure function) behaves correctly.

**Non-goals.** No routing/ranking change; no cache; no budget enforcement; no deadline logic;
no new dependency.

**Completion conditions.** Spans and the `research_metrics` record are persisted for a web
turn; the budget types exist with passing unit tests; all existing diagnostics tests are green;
no answer changes.

**Depends on.** —

---

## M1-T2 — Typed `SourceOutcome` + provider failure classification

**Goal.** Stop conflating a dead/blocked provider with "no results" (limitation L6 of the
architecture doc), using typed per-call outcomes, without adding retries or parallelism.

**Inputs.**
- `app/capabilities/web_search.py` (`SearchBackend`, `last_error`, `FeedSearchBackend`,
  `WikipediaSearchBackend`, `GoogleNewsRSSBackend`).
- `app/capabilities/web_research.py` `_search`, `_classify`, `ToolOutcome` codes.
- `docs/SPEC.md` §13.3 and §10.5 taxonomy.

**Outputs.**
- `SourceOutcome` typed value: `OK | EMPTY | RATE_LIMITED | AUTH_REQUIRED | BLOCKED |
  TIMEOUT | ERROR` per provider call.
- `Artifact.structured_data["source_outcomes"]`.
- `SEARCH_BACKEND_UNAVAILABLE` emitted only when **all distinct attempted providers** fail;
  a genuine empty response stays `NO_RESULTS`; partial failure preserves successful hits.

**Tests (write first).**
- Fake backend returning HTTP 403 → `AUTH_REQUIRED`/`BLOCKED` (not `EMPTY`).
- Fake backend raising timeout → `TIMEOUT`.
- All providers failing → `SEARCH_BACKEND_UNAVAILABLE`.
- All providers responding empty → `NO_RESULTS`.
- One provider failing and one succeeding → success path kept, failure recorded.

**Non-goals.** No retries/backoff; no parallel calls; no new provider; no ranking change.

**Completion conditions.** Typed outcomes are recorded and asserted; existing
`tests/test_cognition_evidence.py` web taxonomy tests remain green.

**Depends on.** M1-T1.

---

## M1-T3 — Benchmark harness + frozen task set + old/new baseline

**Goal.** Produce the M1 measurable baseline (Answer Completion, Evidence Precision, Latency)
against Phase 5B, with a reproducible harness.

**Inputs.**
- `Engine.ask`, persisted diagnostics/artifacts, `RuntimeConfig`.
- Reviewed Phase 5B HEAD `7ad65b2` (old build) and the M1 build (new).
- Frozen representative task set (language-general; includes at least one historical
  `EXPLANATION` question and one current/factual/narrative question).

**Outputs.**
- `tools/benchmark_research.py` (not on the runtime path) with `offline` (fixtures) and `live`
  modes; writes a machine-readable JSON report.
- Frozen task file (`tools/data/research_tasks.json` or equivalent) with task id, query,
  language, expected obligation shape (no expected answer text).
- Report fields per task and aggregate: `answer_completion` (all mandatory propositions
  `SATISFIED` and the answer expresses the mandatory result),
  `evidence_precision` (admitted `SUPPORTED` findings that genuinely establish the proposition
  under subject/metric/scope, rubric-adjudicated or deterministically re-checked for
  structured facts), and `latency` (`web:source:*`, `web:search`, `web:fetch`, `web:verify`,
  `tool:web_research`, `goal_end_to_end` p50/p95 over repeated runs). `information_gain` is
  reported where derivable (coverage delta vs the pre-research artifact state).
- A short rubric document for Evidence Precision adjudication.

**Tests (write first).**
- Harness runs in offline fixture mode deterministically and produces the same report twice.
- Report schema validated; aggregate p50/p95 computed from repeated runs.
- A test asserts the harness never mutates the runtime path and never writes inside
  `app/`.

**Non-goals.** No optimization or performance claims; no CI gating on live network; no
production behavior change; ACR/EP are never traded against each other silently.

**Completion conditions.** Old (`7ad65b2`) and new (M1) baseline reports exist for the frozen
task set with ACR, EP and latency; the report is reproducible; the rubric is documented.

**Depends on.** M1-T1.

---

## M1-T4 — Minimal `SourceDescriptor` / `SearchProvider` contract + keyless Wikipedia adapter

**Goal.** Introduce the minimal source contract needed by later milestones and wire the
already-live Wikipedia source, without changing selection behavior.

**Inputs.**
- `app/capabilities/web_search.py` (`SearchBackend`, `WikipediaSearchBackend`, `build_backends`,
  `fetch_page`).
- `docs/SPEC.md` §13.1, ADR 0026.

**Outputs.**
- `app/web/sources/base.py`: `SourceDescriptor(source_id, kind, discovery, full_body,
  structured, historical, current, auth, trust_weight, latency_class, rate_limit_per_min)` and
  a `SearchProvider` protocol extending `SearchBackend`. `trust_weight` is declared with the
  monotone convention (higher = more trusted).
- A Wikipedia `SourceDescriptor` + adapter exposing search and body fetch; runtime capability
  probe (`available`).
- Additive `Artifact.structured_data["sources"]`.

**Tests (write first).**
- Descriptor construction/validation; the Wikipedia descriptor advertises `historical=True`,
  `full_body=True`, `auth="none"`, `trust_weight` in the documented band.
- Capability probe returns a typed unavailable outcome instead of raising when Wikipedia is
  unreachable.
- Existing web/search tests remain green; selection behavior for non-Wikipedia paths is
  unchanged.

**Non-goals.** No registry-driven selection change; no `trust_weight` use in ranking (M2); no
new providers; no routing change.

**Completion conditions.** The contract and Wikipedia adapter exist behind feature-neutral
wiring; existing tests green; no answer change.

**Depends on.** M1-T1.

---

## M1-T5 — Real Wikipedia historical retrieval + body verification

**Goal.** Demonstrate the first real retrieval gain: a historical `EXPLANATION`/`FACT`
question retrieves a Wikipedia body over the live network and passes the existing evidence
verification, with offsets replayable.

**Inputs.**
- M1-T4 Wikipedia adapter; `fetch_page`; `evidence_spans`; `verify_span`.
- `web_research` inputs from the Planner (`propositions`, `entity_names`, `candidate_names`,
  `objective`).
- A language-general historical prompt (e.g. a past work stoppage). No player/event branch in
  production code.

**Outputs.**
- For historical `EXPLANATION` questions, a `SourcePlan`-lite selects Wikipedia as a primary
  source; the page body is fetched and stored in `RetrievedDocument.body_text`.
- `EvidenceSpan`s with exact offsets and `EvidenceFinding`s from the unchanged verifier.
- Artifact records documents, spans, findings, `retrieval_status`, `source_outcomes`.
- A live probe record attached to the M1 report.

**Tests (write first).**
- Offline fixture: a Wikipedia-shaped body yields offset-valid spans and a verifier finding;
  a page without the subject yields no support.
- Live (`RUN_INTEGRATION=1`): a historical question fetches ≥1 Wikipedia document with
  `retrieval_status=OK`, `body[start:end] == span.text`, and produces ≥1 finding; the goal is
  honestly `COMPLETE` or `LIMITED`, never fabricated.
- A test asserts Judge/Verifier/Composer files are unmodified (checksum or diff guard).

**Non-goals.** No broad-search API; no query-facet expansion (M2); no ranking (M2); no cache
(M3); no budget enforcement (M3); no structured adapter (M4).

**Completion conditions.** The live historical Wikipedia path is green and recorded; the
ACR/EP/Latency baseline in M1-T3 includes this task; verifier/Judge untouched.

**Depends on.** M1-T3, M1-T4, M1-T6.

---

## M1-T6 — Body-version metadata (`body_hash`, `extractor_version`) + `StructuredRecord` contract

**Goal.** Establish the body-version/offset contract and the structured-record envelope as
*contracts and metadata only*, so later cache/replay work cannot reuse stale offsets.

**Inputs.**
- `app/capabilities/web_search.py` `fetch_page`, `extract_text`, `evidence_spans`,
  `_content_hash`.
- `docs/SPEC.md` §13.4, §13.5; ADR 0027, ADR 0030.

**Outputs.**
- Per document: `raw_content_hash` (= existing `RetrievedDocument.content_hash`) and additive
  metadata `body_hash = SHA-256(body_text)` and `extractor_version`.
- A version-match check used on replay/extraction: a `(body_hash, extractor_version)` mismatch
  forces re-extraction instead of offset reuse (test helper in M1; enforcement point wired but
  never bypasses the verifier).
- `app/web/sources/structured.py` declaring the `StructuredRecord` dataclass envelope
  (`record_id`, `source_id`, `source_url`, `as_of`, `conversion_version`, `raw_fields`) with
  its provenance-header rule and `source_quality`/`location` conventions. **No adapter
  implementation in M1.**

**Tests (write first).**
- `body_hash` is stable for identical `(raw_bytes, extractor, extractor_version)` and changes
  when the extracted text changes.
- A mismatched `extractor_version` triggers re-extraction; offsets are not reused.
- `body[start:end] == text` still holds for every admitted span.
- The `StructuredRecord` envelope round-trips and its provenance header rule is unit-tested.

**Non-goals.** No cache read/write or TTL logic (M3); no extractor change; no structured
network adapter (M4); no evidence-contract change.

**Completion conditions.** Version metadata is present and enforced in tests; SPEC §13.4/§13.5
invariants hold; no accepted contract is modified.

**Depends on.** M1-T4.

---

## M1-T7 — Trust-direction + classification regression tests

**Goal.** Lock the two contract directions that later milestones depend on, so an inverted
authority score or a mis-typed failure cannot land silently.

**Inputs.**
- `SourceDescriptor.trust_weight` (M1-T4); `SourceOutcome` (M1-T2); a pure
  `trust_weight`-based comparator/scoring helper.

**Outputs.**
- Regression tests that fail if a lower `trust_weight` outranks a higher one for an identical
  lexical match (official > encyclopedia/reference > publisher news > open web >
  discovery-only).
- Regression tests for the `SourceOutcome` mapping matrix (403/429/timeout/empty/all-fail).

**Tests (write first).** The tests themselves are the deliverable; they must fail against an
inverted implementation.

**Non-goals.** No production ranking use of `trust_weight` (measured in M2); no change to the
selection path in M1.

**Completion conditions.** The direction and classification tests are green and would fail on
an inverted/broken implementation.

**Depends on.** M1-T2, M1-T4.

---

## M1 acceptance checklist

| # | Criterion | Ticket |
|---|---|---|
| 1 | `research_metrics` + per-source latency spans persisted; redaction intact | T1 |
| 2 | Provider failures typed; dead provider ≠ `NO_RESULTS` | T2 |
| 3 | Harness + frozen task set + old-vs-new ACR/EP/Latency baseline | T3 |
| 4 | Minimal source contract + keyless Wikipedia adapter | T4 |
| 5 | **Real Wikipedia historical retrieval + body verification** | T5 |
| 6 | Body-version metadata + `StructuredRecord` contract (no cache) | T6 |
| 7 | Trust-direction + classification regression tests | T7 |
| 8 | Judge / Evidence Verifier / Composer **unmodified** | all |
| 9 | Phase 5B offline suite + independent baseline preserved | all |
| 10 | No M2–M4 behavior implemented | all |

## M2–M4 not to be started

- **M2:** query facets, `CandidateRanker` in the production selection path, bounded parallel
  search/fetch, retry/backoff, `trust_weight` benefit measurement.
- **M3:** budget/deadline enforcement, in-flight cancellation/admission, full URL-keyed cache,
  fact/document freshness TTL behavior.
- **M4:** authenticated broad-search provider, official structured adapter family.

If any M1 ticket appears to require M2–M4 behavior to pass, stop and raise it for review rather
than implementing ahead of schedule.
