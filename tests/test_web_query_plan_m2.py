"""M2-2: requirement/proposition-aware query planning."""

from __future__ import annotations

from app.web.query_plan import build_query_plan, merge_llm_variants, plan_texts


def _prop(pid, statement, kind="EXPLANATION", required="strike causes salary cap", subject="1994 Major League Baseball strike", **overrides):
    value = {
        "id": pid,
        "statement": statement,
        "kind": kind,
        "subject": subject,
        "time_scope": {"season": 1994},
        "population": None,
        "required_information": required,
        "operation": "",
        "mandatory": True,
    }
    value.update(overrides)
    return value


def test_broad_entity_facet_is_last_not_first():
    plan = build_query_plan(
        objective="Explain the causes and impact of the 1994 MLB strike",
        subjects=["1994 Major League Baseball strike"],
        propositions=[_prop("p1", "explain causes"), _prop("p2", "explain impact", required="strike impact effects")],
        user_wording="Explain the causes and impact",
        target_information="causes and impact",
    )
    intents = [variant.intent for variant in plan.variants]
    assert intents[-1] == "ENTITY"
    assert intents[0] in {"EVENT", "RELATION", "METRIC", "INTERVIEW", "FRESHNESS"}
    # The broad subject-only facet must not precede the relationship facets.
    assert [v.text for v in plan.variants if v.intent == "ENTITY"] == ["1994 Major League Baseball strike"]


def test_relationship_variant_maps_to_proposition_id_and_scope():
    plan = build_query_plan(
        objective="strike causes",
        subjects=["1994 Major League Baseball strike"],
        propositions=[_prop("p1", "explain causes")],
    )
    relation = next(v for v in plan.variants if v.target_proposition_ids)
    assert relation.target_proposition_ids == ("p1",)
    assert "1994" in relation.text
    assert "salary cap" in relation.text


def test_cause_and_impact_facets_are_generated():
    plan = build_query_plan(
        objective="What caused the strike and what were its effects?",
        subjects=["1994 Major League Baseball strike"],
        propositions=[_prop("p1", "explain causes and effects")],
        user_wording="causes and effects",
    )
    texts = plan_texts(plan)
    assert any("causes" in text for text in texts)
    assert any("impact" in text for text in texts)


def test_facets_missing_records_uncovered_mandatory_propositions_without_subject():
    plan = build_query_plan(
        objective="explain",
        subjects=[],
        propositions=[_prop("p1", "explain causes")],
    )
    assert "p1" in plan.facets_missing


def test_llm_variants_append_but_do_not_displace_deterministic_facets():
    base = build_query_plan(
        objective="strike causes",
        subjects=["1994 Major League Baseball strike"],
        propositions=[_prop("p1", "explain causes")],
    )
    merged = merge_llm_variants(base, ["1994 MLB strike salary cap cause"])
    assert merged.generated_by == "deterministic+llm"
    assert plan_texts(merged)[: len(base.variants)] == plan_texts(base)
    assert "1994 MLB strike salary cap cause" in plan_texts(merged)


def test_multilingual_variant_only_when_policy_expands():
    english_only = build_query_plan(
        objective="injury status",
        subjects=["Shohei Ohtani"],
        research_languages=["en"],
    )
    assert all(v.language == "en" for v in english_only.variants)
    expanded = build_query_plan(
        objective="日本媒体怎么看大谷翔平的伤病",
        subjects=["Shohei Ohtani"],
        aliases=["大谷翔平"],
        research_languages=["en", "ja"],
        expansion_reasons=["likely_primary_source_language:ja"],
    )
    assert any(v.language == "ja" for v in expanded.variants)


def test_freshness_class_from_high_risk_current_question():
    plan = build_query_plan(
        objective="current injury report",
        subjects=["Aaron Judge"],
        propositions=[_prop("p1", "find recent injury reports", kind="FACT", required="recent injury", subject="Aaron Judge", time_scope={})],
        user_wording="recent injury report",
    )
    assert plan.freshness == "VOLATILE"
    assert any(v.intent == "FRESHNESS" for v in plan.variants)


def test_historical_question_is_immutable():
    plan = build_query_plan(
        objective="What caused the 1994 strike?",
        subjects=["1994 Major League Baseball strike"],
        propositions=[_prop("p1", "explain causes")],
    )
    assert plan.freshness == "IMMUTABLE"
