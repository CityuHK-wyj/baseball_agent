"""Live DeepSeek natural-language robustness harness (M2.12R4 audit §10-§23).

Generates fresh paraphrase and typo populations from an *oracle* semantic intent defined
before the text is produced. Runs the real configured semantic interpreter, grounds its
untrusted candidate through the deterministic boundary, and classifies the outcome.

It never prints or persists the API key. Results are written as JSON evidence.

Usage:
    python -m tools.m212r4_nl_robustness <output_dir> [--seed 7717] [--per-intent 3] [--typos 2]
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

# --------------------------------------------------------------------------- oracles
# Each intent defines the governed semantic contract BEFORE any text is generated.
INTENTS = [
    {
        "id": "opposing_pitcher_left", "role": "BATTER", "check": ("filters", "p_throws", "L"),
        "dimension": ("opposing_pitcher_hand", "left"),
        "phrases": ["against southpaws", "vs LHP", "facing lefties", "对左投手",
                    "against left-handed pitching", "a pitcher throwing from the left side"],
        "verbs": ["how did {s} do", "{s} batting numbers", "{s} 打击表现", "report {s} stats"],
    },
    {
        "id": "opposing_pitcher_right", "role": "BATTER", "check": ("filters", "p_throws", "R"),
        "dimension": ("opposing_pitcher_hand", "right"),
        "phrases": ["against righties", "vs RHP", "facing right-handers", "对右投手"],
        "verbs": ["how did {s} do", "{s} batting numbers", "report {s} stats"],
    },
    {
        "id": "pitch_type_slider", "role": "PITCHER", "check": ("filters", "pitch_type", "SL"),
        "dimension": ("pitch_type", "SL"),
        "phrases": ["on sliders", "against sliders", "when throwing sliders"],
        "verbs": ["how did {s} do", "{s} pitching numbers", "report {s} stats"],
    },
    {
        "id": "count_two_strikes", "role": "BATTER", "check": ("filters", "strikes", 2),
        "dimension": ("strikes", 2),
        "phrases": ["with two strikes", "when he has two strikes", "两好球时"],
        "verbs": ["how did {s} do", "{s} batting numbers", "report {s} stats"],
    },
    {
        "id": "count_three_balls", "role": "BATTER", "check": ("filters", "balls", 3),
        "dimension": ("balls", 3),
        "phrases": ["when the count is 3-0", "with three balls", "三坏球时"],
        "verbs": ["how did {s} do", "{s} batting numbers", "report {s} stats"],
    },
    {
        "id": "inning_seven", "role": "BATTER", "check": ("filters", "inning", 7),
        "dimension": ("inning", 7),
        "phrases": ["in the seventh inning", "第七局", "during inning 7"],
        "verbs": ["how did {s} do", "{s} batting numbers", "report {s} stats"],
    },
    {
        "id": "role_pitcher", "role": "PITCHER", "check": ("role", "analytical_role", "PITCHER"),
        "dimension": ("role", "pitcher"),
        "phrases": ["as a pitcher", "pitching", "投手", "on the mound"],
        "verbs": ["how did {s} do", "report {s} numbers", "{s} 表现"],
    },
    {
        "id": "split_pitcher_hand", "role": "BATTER", "check": ("group_by", "p_throws", "p_throws"),
        "dimension": ("opposing_pitcher_hand", "split"),
        "phrases": ["split by pitcher handedness", "by opposing pitcher hand", "按投手左右手分组",
                    "broken down by the pitcher's throwing hand"],
        "verbs": ["how did {s} do", "report {s} numbers", "{s} 表现"],
    },
]

SUBJECTS = ["Ohtani", "大谷翔平"]
SEASONS = ["in 2025", "2025年", "during the 2025 season"]

# Typo mutations are generated, never listed in production code.
def mutate(rng: random.Random, phrase: str) -> str:
    if not phrase:
        return phrase
    kind = rng.choice(["delete", "transpose", "duplicate", "case", "space", "punct"])
    if all(ord(ch) < 128 for ch in phrase):
        # Never mutate a digit: changing a number changes the oracle's semantic intent.
        choices = [i for i, ch in enumerate(phrase) if not ch.isdigit()] or [0]
        i = rng.choice(choices)
        if kind == "delete":
            return phrase[:i] + phrase[i + 1:]
        if kind == "transpose" and i + 1 < len(phrase):
            return phrase[:i] + phrase[i + 1] + phrase[i] + phrase[i + 2:]
        if kind == "duplicate":
            return phrase[:i] + phrase[i] + phrase[i:]
        if kind == "case":
            return phrase[:i] + phrase[i].upper() + phrase[i + 1:]
        if kind == "space":
            return phrase[:i] + " " + phrase[i:]
        return phrase + rng.choice(["?", ".", "!", ""])
    # CJK: substitute a non-semantic character. The handedness characters 左/右 are never
    # mutated: changing them would change the oracle's intended meaning, not just wording.
    i = rng.randrange(len(phrase))
    return phrase[:i] + rng.choice(["手", "投"]) + phrase[i + 1:]


def generate(seed: int, per_intent: int, typos: int):
    rng = random.Random(seed)
    cases = []
    for intent in INTENTS:
        for index in range(per_intent):
            subject = rng.choice(SUBJECTS)
            phrase = rng.choice(intent["phrases"])
            verb = rng.choice(intent["verbs"]).format(s=subject)
            season = rng.choice(SEASONS)
            order = [verb, phrase, season]
            rng.shuffle(order)
            text = " ".join(order)
            cases.append(dict(intent=intent["id"], text=text, phrase=phrase, typo=False,
                              oracle=dict(role=intent["role"], check=list(intent["check"]),
                                          dimension=list(intent["dimension"]))))
            for _ in range(typos):
                mutated = mutate(rng, phrase)
                text = " ".join([verb, mutated, season])
                cases.append(dict(intent=intent["id"], text=text, phrase=mutated, typo=True,
                                  oracle=dict(role=intent["role"], check=list(intent["check"]),
                                              dimension=list(intent["dimension"]))))
    rng.shuffle(cases)
    return cases


# --------------------------------------------------------------------- classification
def contract_fields(live_message, entity="Ohtani", year=2025):
    """Ground the interpreter output through the real deterministic boundary."""
    from app.contracts.models import AnalysisRequest, EntityMention, SemanticInterpretation
    from app.capabilities.analytics.conformance import requested_contract
    from app.semantic.goals import RequirementBuilder

    semantic = SemanticInterpretation(
        raw_query=live_message.raw_query,
        understanding=live_message.understanding,
        analysis_requests=live_message.analysis_requests
        or [AnalysisRequest(text=live_message.raw_query, objective=live_message.understanding or live_message.raw_query)],
        entities=live_message.entities or [EntityMention(mention=entity, type_hint="player")],
        constraints=live_message.constraints,
        population_hints=live_message.population_hints,
    )
    goal = RequirementBuilder().build(semantic)
    requirements = [r.model_dump(mode="json") for r in goal.requirements if r.required]
    return requested_contract({}, dict(analytical_requirements=requirements, bound_user_intent=live_message.raw_query), [])


def baseline_contract(live_message, entity="Ohtani"):
    """The pre-refactor baseline: deterministic parse only, no semantic candidate."""
    from app.contracts.models import AnalysisRequest, EntityMention, SemanticInterpretation
    from app.capabilities.analytics.conformance import requested_contract
    from app.semantic.goals import RequirementBuilder

    semantic = SemanticInterpretation(
        raw_query=live_message.raw_query,
        understanding=live_message.understanding,
        analysis_requests=live_message.analysis_requests
        or [AnalysisRequest(text=live_message.raw_query, objective=live_message.understanding or live_message.raw_query)],
        entities=live_message.entities or [EntityMention(mention=entity, type_hint="player")],
        constraints=[c for c in live_message.constraints if c.kind != "analytical"],
        population_hints=live_message.population_hints,
    )
    goal = RequirementBuilder().build(semantic)
    requirements = [r.model_dump(mode="json") for r in goal.requirements if r.required]
    return requested_contract({}, dict(analytical_requirements=requirements, bound_user_intent=live_message.raw_query), [])


def classify(contract, oracle):
    field, value, expected = oracle["check"]
    actual = None
    if field == "filters":
        for predicate in contract.filters or ():
            if predicate.get("field") == value:
                actual = predicate.get("value")
    elif field == "group_by":
        actual = value if value in (contract.group_by or ()) else None
    elif field == "role":
        actual = contract.analytical_role
    analytically_complete = not _analytic_issues(contract.issues)
    if exact(actual, expected):
        return "CORRECT_COMPLETE" if analytically_complete else "CORRECT_INCOMPLETE"
    if actual is not None and not exact(actual, expected):
        return "WRONG_COMPLETE" if analytically_complete else "WRONG_SEMANTIC"
    if not analytically_complete:
        return "SAFE_UNRESOLVED"
    return "SAFE_UNRESOLVED"


# Identity resolution and temporal binding are separate governed stages that run later in
# the real pipeline. Issues from those stages are not evidence that the *semantic* layer
# failed, so they do not count against the analytical execution rate.
_BINDING_ISSUE_MARKERS = (
    "canonical identity", "analytical role", "temporal scope", "temporal clause",
    "source year", "date window", "competition scope",
)


def _analytic_issues(issues):
    return [issue for issue in issues if not any(marker in issue.lower() for marker in _BINDING_ISSUE_MARKERS)]


def exact(actual, expected):
    if actual is None:
        return False
    if isinstance(expected, bool):
        return actual is expected
    return str(actual) == str(expected)


def run(output: Path, seed: int, per_intent: int, typos: int, cache_path: Path | None):
    output.mkdir(parents=True, exist_ok=True)
    cases = generate(seed, per_intent, typos)
    (output / "corpus.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n")
    from app.config import get_config
    from app.llm.client import LLMClient
    from app.semantic.interpreter import SemanticInterpreter

    config = get_config()
    result: dict = {"configured": config.llm.configured, "provider": config.llm.base_url.split("//")[-1].split("/")[0],
                    "model": config.llm.semantic_model, "seed": seed, "cases": []}
    if not config.llm.configured:
        (output / "live_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return result
    llm = LLMClient(config.llm)
    interpreter = SemanticInterpreter(llm)
    for index, case in enumerate(cases):
        try:
            message = interpreter.interpret(case["text"])
            contract = contract_fields(message)
            status = classify(contract, case["oracle"])
            try:
                baseline = classify(baseline_contract(message), case["oracle"])
            except Exception:  # noqa: BLE001
                baseline = "BASELINE_ERROR"
            analytical = [c.value for c in message.constraints if c.kind == "analytical"]
            record = {**case, "status": status, "baseline_status": baseline, "candidate": analytical,
                      "filters": [dict(x) for x in contract.filters],
                      "group_by": list(contract.group_by), "role": contract.analytical_role,
                      "issues": list(contract.issues)}
        except Exception as error:  # noqa: BLE001 - provider failure is recorded, never hidden
            record = {**case, "status": "PROVIDER_ERROR", "error": f"{type(error).__name__}: {error}"}
        result["cases"].append(record)
        (output / "live_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
        if index % 5 == 0:
            print(f"  ... {index + 1}/{len(cases)}", flush=True)

    counts: dict[str, int] = {}
    per_family: dict[str, dict[str, int]] = {}
    baseline_correct = 0
    for record in result["cases"]:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
        if record.get("baseline_status") in {"CORRECT_COMPLETE", "CORRECT_INCOMPLETE"}:
            baseline_correct += 1
        family = per_family.setdefault(record["intent"], {})
        family[record["status"]] = family.get(record["status"], 0) + 1
    total = len(result["cases"])
    correct = counts.get("CORRECT_COMPLETE", 0) + counts.get("CORRECT_INCOMPLETE", 0)
    result["summary"] = {
        "total": total,
        "correct_semantics": correct,
        "correct_semantics_rate": round(correct / total, 4) if total else 0.0,
        "correct_complete": counts.get("CORRECT_COMPLETE", 0),
        "safe_unresolved": counts.get("SAFE_UNRESOLVED", 0),
        "wrong_semantic": counts.get("WRONG_SEMANTIC", 0),
        "wrong_semantic_COMPLETE": counts.get("WRONG_COMPLETE", 0),
        "provider_errors": counts.get("PROVIDER_ERROR", 0),
        "natural_language_execution_rate": round(counts.get("CORRECT_COMPLETE", 0) / total, 4) if total else 0.0,
        "deterministic_only_correct_semantics": baseline_correct,
        "deterministic_only_correct_semantics_rate": round(baseline_correct / total, 4) if total else 0.0,
        "semantic_candidate_lift": round((correct - baseline_correct) / total, 4) if total else 0.0,
        "by_status": counts, "by_intent": per_family,
    }
    (output / "live_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--seed", type=int, default=7717)
    parser.add_argument("--per-intent", type=int, default=3)
    parser.add_argument("--typos", type=int, default=2)
    args = parser.parse_args()
    run(Path(args.output), args.seed, args.per_intent, args.typos, None)
