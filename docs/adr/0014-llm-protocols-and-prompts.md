# LLM implementations behind the existing Protocols, with versioned prompts

Status: accepted

## Context

Posts 04/05/09 and the brief require LLM Planner, Judge and Response behind Protocols,
with the deterministic implementations retained as the tested default and fallback (§30,
§54, §55), provider-agnostic model configuration (§56) and versioned, auditable prompts
(§57). No provider abstraction or LLM implementation existed.

## Decision

- `app/llm/provider.py`: `ModelProvider` Protocol, `ModelResponse`, `ProviderError` /
  `ProviderTimeout`, and a scripted `FakeModelProvider`. The domain layer depends only on
  the Protocol.
- `app/config.py`: per-agent model selection from the environment
  (`PLANNER_MODEL`, `JUDGE_MODEL`, `RESPONSE_MODEL`, `SEMANTIC_MODEL`, and
  `LLM_TIMEOUT_SECONDS`), defaulting to `DEEPSEEK_MODEL`. API keys stay in the
  environment and are hidden from `repr`.
- `app/llm/prompts.py`: `PromptTemplate(prompt_id, version, template, required_variables)`
  with literal (non-`str.format`) rendering, so JSON braces in prompts are safe. The
  PLANNER/JUDGE/RESPONSE templates are versioned and identifiable.
- `app/llm/parsing.py`: `parse_json_object` tolerates fenced code blocks and rejects
  non-JSON/non-object output.
- `app/llm/planner.py`: `LLMPlanner` validates the structured decision (valid kind,
  requirement refs ⊆ context requirements, terminal contract enforced by
  `PlanningDecision`). It may not run after a terminal decision and falls back to a
  deterministic planner on provider/parse/invariant failure.
- `app/llm/judge.py`: `LLMJudge` returns `REJECT` for a hard deterministic failure
  **without calling the provider**, and falls back to `RuleBasedJudge` on failure.
- `app/llm/response.py`: `LLMResponseComposer` emits text from accepted evidence only,
  with a `DeterministicResponseComposer` fallback. It never re-derives facts or replans.
- `app/llm/openai_provider.py`: the only module importing a vendor SDK, imported lazily;
  it redacts credentials from any provider error.

## Alternatives

- Let one provider SDK leak through the domain layer: rejected; it would couple core
  contracts to a vendor.
- Replace the deterministic implementations with LLM-only: rejected; they are the
  tested default and the fallback.
- Inline long prompts in functions: rejected by §57.

## Consequences

- Model behavior is configuration-driven and swappable; tests use `FakeModelProvider`
  and make no network calls.
- No LLM implementation is wired into the Orchestrator yet; it remains an explicit,
  injectable choice.
- `LLMSemanticNormalizer` is not implemented; the semantic layer stays deterministic
  for now, with the `ObjectiveExtractor` Protocol available for an LLM version.
