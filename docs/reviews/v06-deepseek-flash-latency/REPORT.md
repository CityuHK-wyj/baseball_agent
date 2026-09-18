# v0.6 DeepSeek Flash runtime & context-growth report

**Branch:** `pi/v0.6-deepseek-flash-latency`
**Baseline (immutable):** `pi/v0.5-planner-convergence` audit checkpoint
`4233984973670d7abcbab850fc38fb3685ce413c` (parent implementation
`899957846805347453accfc34f74ab53f86ddcb3`).
**Mode:** product-path model migration to `deepseek-flash` + bounded LLM orchestration +
context-growth measurement + the v0.5 orchestration correctness fix. No merge, no tag,
`main` untouched.

## 1. Exact model configuration

```
base_url = https://api.deepseek.com
runtime_model = deepseek-flash        # DEFAULT_RUNTIME_MODEL
DEEPSEEK_MODEL = deepseek-flash       # authoritative default
# resolution: RUNTIME_MODEL -> DEEPSEEK_MODEL -> deepseek-flash
semantic = deepseek-flash  max_tokens=1024
planner  = deepseek-flash  max_tokens=2048
response = deepseek-flash  max_tokens=1024
judge    = deepseek-flash  max_tokens=512   (no LLM Judge is wired; deterministic)
reasoning_effort = none    # structured roles emit typed output within the bound
stream = true              # first-token observable; deadline-abortable
deadline = 90s             # true wall clock (request + wait + stream + retry)
```

## 2. LLM call inventory (Part A)

| Role | Call site | Provider | Model source | Default | Override | Bound | Reasoning | Streaming | Retry |
|---|---|---|---|---|---|---|---|---|---|
| Semantic | `artifact_runtime/planner.py:83` (`LLMSemanticInterpreter.brief`) | `OpenAICompatibleProvider` | `llm_semantic_model` | deepseek-flash | `SEMANTIC_MODEL` | 1024 | none | yes | none |
| Planner initial | `planner.py:442` (`LLMPlanner.initial_needs`) | same | `llm_planner_model` | deepseek-flash | `PLANNER_MODEL` | 2048 | none | yes | none |
| Planner replan | `planner.py:463` (`LLMPlanner.add_needs`) | same | `llm_planner_model` | deepseek-flash | `PLANNER_MODEL` | 2048 | none | yes | none |
| Response composer | `artifact_runtime/response.py:80` | same | `llm_response_model` | deepseek-flash | `RESPONSE_MODEL` | 1024 | none | yes | none |
| Judge | `artifact_runtime/sufficiency.py` | **none** | `llm_judge_model` | deepseek-flash | `JUDGE_MODEL` | 512 | none | — | — |
| Legacy dual extractor | `semantic/semantic_extractor.py:433` | same | `semantic_extractor_model` | deepseek-flash | `SEMANTIC_EXTRACTOR_MODEL` | — | none | yes | none |
| Legacy dual reviewer | `semantic/dual_parser.py` → `LLMEvidenceExtractor`-style reviewer | same | `semantic_reviewer_model` | deepseek-flash | `SEMANTIC_REVIEWER_MODEL` | — | none | yes | none |
| Legacy agent cognition | `agent/cognition.py:145,159` | same | `llm_planner_model` / `llm_response_model` | deepseek-flash | env | — | none | yes | none |
| Doctor probe | `diagnostics.py:148` | same | semantic roles | deepseek-flash | env | — | none | yes | none |
| Test-only / unused | `llm/planner.py`, `llm/response.py`, `llm/judge.py`, `llm/evidence.py` | injected fake/same | constructor arg | — | caller | — | — | — | — |

Product path is the **Artifact Runtime** (`ask`/`chat` → `build_runtime`). The CLI `analyze`
pipeline is legacy; the `app/agent/` conversational runtime is defined but not dispatched
by the CLI. All of them now default to `deepseek-flash`.

## 3. Before / after benchmark (Part S)

Baseline = committed v0.5 audit with `deepseek-v4-pro` (the audit capped live calls at a
diagnostic 110 s/call; the uncapped product reached 710.8 s on one planner call).

| Class | Old model calls | Old end-to-end | New model calls | New end-to-end | Speed-up |
|---|---:|---:|---:|---:|---:|
| simple turn (`simple_local`) | 1 | 100.9 s | 1 | **4.5 s** | 22× |
| clarification t1 | 1 | 100.9 s | 1 | **3.1 s** | 32× |
| clarification t2 (resume) | baseline had no resume (a follow-up t2 with a full semantic call cost 263.2 s) | — | **2 (no semantic call)** | **5.1 s** | — |
| analytical | not measured live in the baseline | — | 4 | **14.1 s** | — |
| multi-tool (`multistep`) | not measured live in the baseline | — | 5 | **20.6 s** | — |
| replan turn (`db_to_web`) | not measured live in the baseline | — | 3 | **12.6 s** | — |
| long conversation (turn 8) | not run in the baseline | — | 3 | **14.5 s** | — |
| web unavailable | 3 | 292.2 s | 4 | **13.3 s** | 22× |
| entity ambiguity | 4 | 351.0 s | 3 | **28.0 s** | 13× |

Only the classes measured in both rounds are compared directly; the remaining new
measurements are reported without an invented baseline.

Model-call timings (median / max):

| Role | Old (deepseek-v4-pro) | New (deepseek-flash) |
|---|---|---|
| semantic | 100.9 s / 110 s (uncapped 92.4 s) | **3.54 s / 5.10 s** |
| planning | 110 s / 110 s (uncapped 710.8 s) | **3.94 s / 7.42 s** |
| response | 17.5 s / 20.8 s | **2.11 s / 2.13 s** |
| completion tokens (planner) | up to **15,856** | median **584**, max **1,904** |
| completion tokens (semantic) | median 2,143 | median **476** |

Prompt sizes are essentially unchanged (the runtime still sends the same context):
planner prompt median **13,728 chars**, max **24,312 chars**; semantic prompt median
1,345 chars.

## 4. Context-growth findings (Parts E–I)

Measured with the product runtime (scripted cognition, exact serialized inputs); see
`context_growth.json`.

* **Who receives conversation history:** only the **semantic interpreter**, and only the
  last `_MAX_HISTORY = 8` messages. The planner and composer receive **no conversation
  history**.
* **Who receives artifacts:** planner (all exports of all artifacts in the conversation)
  and composer. The semantic interpreter receives none.
* **Who receives tool outcomes:** planner (last 12 `AttemptView`s in `PlannerFeedback`).
* **Growth turn 1 → 16:**
  * semantic prompt 1,543 → 2,503 chars (+960; plateaus once history hits 8 messages);
  * planner prompt 10,576 → 14,272 chars (+3,696): exports 1 → 16 (+2,325 chars) and
    feedback 187 → 1,558 chars (+1,371) are the only growth vectors. Needs, schema and
    capabilities are rebuilt per Goal revision and do not accumulate.
* **Composition (turn 16):** schema **4,279 chars** + capabilities **1,894 chars** =
  **60.4 %** of the planner context; exports 2,488 (24 %); feedback 1,558 (15 %).
  At turn 1 static schema+capabilities were ~95 % of the context.
* **Duplication (Part H):** the current query appears **once** in the planner prompt (the
  Goal statement) and **zero** times in the schema/capability/export/feedback context.
  There is no accidental quadruple repetition. The semantic prompt repeats prior messages
  by design, bounded to 8.
* **Does latency grow with input size (Part G)?** No. A controlled provider probe with the
  same model and bounded output:

  | prompt chars | prompt tokens | duration |
  |---:|---:|---:|
  | 852 | 134 | 2.35 s (cold) |
  | 2,052 | 311 | 3.70 s (cold) |
  | 6,052 | 899 | 1.70 s |
  | 12,052 | 1,781 | 1.65 s |
  | 24,052 | 3,545 | 1.94 s |

  From 6 k to 24 k input chars latency is flat (1.65–1.94 s). Consistent with the live
  long-conversation turns, which show **no upward latency trend** (t2 17.4 s, t6 14.2 s,
  t8 14.5 s). **Completion length, not input context, was the latency driver.**

## 5. Planner prompt composition (Part I)

Every Planner call receives **all** capabilities (8), **all** schema tables (4) and all
their fields, all available exports, and up to 12 attempts — there is no relevance
selection. Python construction is ~1 ms; the cost is prompt tokens. v0.5's expanded
convergence surface is therefore a **fixed ~6.2 k-char context tax** per planner call,
plus growing exports/attempts. The new bounds cap the *response*; the input is unchanged.

## 6. Model-call count changes (Parts L/M)

* Replans are now bounded to **2 per turn** and repeated identical gap signatures are a
  deterministic continuation. Alternate-route recovery is preserved (regression tests).
  The live benchmark observed ≤ 2 replans per turn.
* Clarification selection resumes from the persisted brief: live `clarification_resume`
  turn 2 recorded `CLARIFICATION_RESUMED` with **2 model calls and no semantic call**,
  versus a full semantic + planner reconstruction before.

## 7. Output limits (Part J)

Reasoning tokens count against `max_tokens`; with `reasoning_effort=none` the structured
roles emit their JSON within the bound. Observed planner completion max 1,904 tokens
(previously 15,856). A `finish_reason=length` with empty content is a typed failure, not an
empty success.

## 8. Deadline behaviour (Part K)

The provider streams and enforces a wall-clock deadline over request + wait + stream, and
the SDK per-operation timeout is additionally bounded by the deadline. On overflow it
raises `ProviderTimeout`; the semantic layer falls back to `RuleBasedSemanticInterpreter`,
the planner to `DeterministicPlanner`, the composer to the deterministic composer. A
forced `LLM_DEADLINE_SECONDS=0.01` run completed in 4.5 s with the deterministic fallback
plan and no hang. Unit tests cover mid-stream deadline abort and empty-length failure.

## 9. Orchestration correctness fix (Part P)

A failed unresolved CORE Need is no longer excluded from `missing` unconditionally. It is
credited only for an explicit alternate route or an independently verified frozen
obligation; otherwise it keeps `core_goal_supported=false` so the planner is asked to
recover. Regression tests:
`test_failed_unresolved_core_need_is_not_credited_without_evidence`,
`test_failed_core_need_is_asked_to_recover_when_no_obligations_exist`,
`test_verified_obligation_credits_a_closed_core_need`,
`test_alternate_route_satisfies_the_original_obligation`.

## 10. Native latency trace example (Part R)

```
$ python3 -m app.cli chat --trace
Latency
  semantic                    0.0 ms
  goal_construction           0.1 ms
  planner_total               5.4 ms
  planner_initial             0.0 ms
  planner_replan              0.0 ms
  planner_context             1.0 ms
  planner_scheduler           0.0 ms
  binding_resolution          0.0 ms
  tool_execute                3.3 ms
  ir_compile                  2.5 ms
  db_execute                  0.0 ms
  scope_verification          0.0 ms
  judge                       0.2 ms
  verification                0.0 ms
  claim_grounding             0.0 ms
  state_projection            0.0 ms
  response_composition        0.0 ms
  persistence                 0.0 ms
  total(measured)            12.7 ms
```

`LATENCY` events are per-turn (`RuntimeTrace.latency`), store only a phase name and a
measured duration, and never contain prompts, payloads or hidden reasoning.

## 11. Test results

* Full suite (excluding the PostgreSQL integration file, whose server is down in this
  environment): **724 passed, 164 subtests passed**.
* `tests/artifact_runtime` (v0.4 invariants + v0.5 convergence + new regressions):
  **168 passed**.
* New `tests/llm/test_bounded_provider.py`: streaming collection, deadline abort,
  empty-length failure, role-bound propagation.
* New config tests: `deepseek-flash` default, role table, shared/override resolution.
* `compileall`: clean. Secret scan: clean.
* Known pre-existing failures: `tests/integration/test_analytics_integration.py`
  (4 tests) require live PostgreSQL at `127.0.0.1:5433`, which is **down** in this
  environment; they fail identically on the baseline. Not caused by this round.

## 12. Answers to the 20 questions

1. **Is deepseek-flash used by every normal product-path call?** Yes — semantic, planner
   initial, planner replan and response composer; the Judge is deterministic (no model).
2. **Components on another model?** None by default; legacy roles also default to the
   shared runtime model. Test-only modules accept injected fakes.
3. **Model calls per class?** simple 1; analytical 4; multi-tool 5; replan 3; clarification
   1 then 2 (resume); long-conversation 1–4.
4. **Planner input size?** 11,090–24,312 chars (median 13,728).
5. **Conversation-history share of Planner input?** 0 %.
6. **Schema/capability/artifact context?** schema 4,279 + capabilities 1,894 chars static;
   exports and feedback grow with the conversation.
7. **Does prompt size grow with conversation length?** Yes for the Planner (exports +
   feedback) and mildly for the semantic layer (history, bounded to 8 messages).
8. **Does model latency grow with input-context size?** No (6 k→24 k chars flat at
   1.65–1.94 s).
9. **Does completion length grow with conversation length?** No material trend.
10. **Was semantic content duplicated?** No heavy duplication; the query appears once in
    the Planner prompt.
11. **What caused the 15,856-token completion?** Hidden reasoning under an unbounded
    reasoning model — not prompt size.
12. **Role-specific limits?** semantic 1024, planner 2048, response 1024, judge 512.
13. **True total deadline?** Yes (streaming + deadline-bounded SDK timeout).
14. **On deadline exceeded?** Typed `ProviderTimeout` → safe deterministic fallback; never
    silent success.
15. **Replans eliminated?** Replans bounded to 2/turn and identical gaps skipped; recovery
    preserved.
16. **Clarification resume?** Yes, deterministic for option selections; free text falls
    back to semantic interpretation.
17. **Invariant regression?** None; the orchestration fix strengthens CORE-Need handling.
18. **End-to-end improvement?** 13–32× on the directly-comparable classes
    (100.9 s → 4.5 s; 292.2 s → 13.3 s; 351.0 s → 28.0 s).
19. **Remaining provider-controlled latency?** Model inference itself (~2–7 s per call).
20. **Unresolved problems?** Semantic over-clarification and the "Pittsburgh Pirates"
    hallucination; planner quality/replan count; non-LLM network tools (statsapi,
    pybaseball) can still dominate a turn (up to ~12 s); Web still blocked by the SSRF
    policy against the environment's fake-IP DNS.

## 13. Known limitations

* The live benchmark ran 1 sample per scenario (multi-turn for follow-up and long
  conversation) under normal product behavior; provider latency still varies by a few
  seconds.
* The `deepseek-flash` default disables hidden reasoning for structured roles. Quality is
  preserved by the deterministic boundaries, but a deployment that wants more model
  reasoning can set `LLM_REASONING_EFFORT=low|medium|high` at a latency cost.
* Web research remains unavailable because of the environment network (see the v0.5 audit);
  it was neither weakened nor counted as a model problem.
* `tests/integration/test_analytics_integration.py` needs a live PostgreSQL instance.

`DEEPSEEK_FLASH_RUNTIME_OPTIMIZED — READY_FOR_EXTERNAL_LATENCY_DOGFOODING`
