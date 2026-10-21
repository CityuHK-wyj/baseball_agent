"""Qualified partial presentation: useful non-authoritative evidence stays visible.

Authority determines what may be asserted as fact; presentation decides what useful evidence
the user may see. A qualified entry is never a Claim, never satisfies a Requirement and never
makes the Goal COMPLETE. Hard-invalid or irrelevant material still stays hidden.
"""

from __future__ import annotations

from app.contracts.models import (
    Artifact,
    CoverageState,
    GoalStatus,
    Reference,
    ReferenceType,
    SemanticInterpretation,
    VerificationStatus,
)
from tools.requirement_ancestry_fixture import Goal
from app.response.composer import ResponseComposer
from app.verification.evidence import verify_span
from tests.test_source_authority_wsa03 import _end_to_end, _goal_for, _prop, _role_llm
from tests.test_web_evidence_semantics_wsa01_02 import _document, _judge, _span, _web_artifact


def _multi_artifact(goal, proposition, entries) -> Artifact:
    """One WEB_EVIDENCE artifact carrying several (document, span, finding) records."""
    findings, spans, documents, raw_findings = [], [], [], []
    for document, span, finding in entries:
        findings.append(finding)
        spans.append({**span.model_dump(mode="json"), "quality": "prose", "span_score": 1.0})
        documents.append(document.model_dump(mode="json"))
        raw_findings.append({
            "document_id": document.id, "url": document.url, "final_url": document.final_url,
            "title": document.title, "retrieved_at": document.retrieved_at,
            "publication_date": document.publication_date, "content_hash": document.content_hash,
            "evidence_spans": [{**span.model_dump(mode="json"), "quality": "prose", "span_score": 1.0}],
            "proposition_findings": [finding.model_dump(mode="json")],
            "proposition_ids": [proposition.id], "relevant": True, "grounded": True,
        })
    return Artifact(
        id="art_web_multi", kind="WEB_EVIDENCE", need_id="n_web",
        structured_data={
            "objective": "research", "findings": raw_findings, "documents": documents,
            "evidence_spans": spans, "proposition_verification": True,
            "evidence_findings": [f.model_dump(mode="json") for f in findings],
        },
        references=[Reference(ref_type=ReferenceType.WEB_EVIDENCE, ref_id=documents[0]["url"],
                               source=documents[0]["url"], span=spans[0]["text"])],
        status="PARTIAL",
    )


def _verify_pair(proposition, text, publication, llm):
    document = _document(text, publication)
    span = _span(text, document)
    finding = verify_span(proposition, span, document, llm=llm, allow_llm=True)
    return document, span, finding


# ---------------------------------------------------------------- speculative / stale


def test_speculative_report_is_shown_but_stays_speculative():
    proposition = _prop("signing transaction", info="TRANSACTION")
    text = "Shohei Ohtani signing transaction: a report says the Dodgers may pursue him."
    llm = _role_llm(source_class="NEWS", content_role="REPORTING", evidential_basis="SECONDARY",
                    strength="SPECULATIVE", status="SUPPORTED")
    projection = _end_to_end(proposition, text, llm)
    assert projection.goal_status.value != "COMPLETE"
    assert not [c for c in projection.claims if proposition.id in c.proposition_ids]
    qualified = [q for q in projection.qualified_evidence if q.proposition_id == proposition.id]
    assert qualified
    assert qualified[0].kind == "ASSERTION"
    assert qualified[0].assertion_strength == "SPECULATIVE"
    assert "speculat" in qualified[0].limitation.lower() or "asserted" in qualified[0].limitation.lower()


def test_stale_source_is_shown_as_explicitly_historical():
    proposition = _prop("injury status", info="INJURY")
    text = "Shohei Ohtani injury status: he was on the injured list at that time."
    llm = _role_llm(source_class="NEWS", content_role="REPORTING", evidential_basis="SECONDARY",
                    strength="ASSERTED", status="SUPPORTED")
    projection = _end_to_end(proposition, text, llm, publication="2019-05-01")
    assert projection.goal_status.value != "COMPLETE"
    qualified = [q for q in projection.qualified_evidence if q.proposition_id == proposition.id]
    assert qualified
    assert qualified[0].kind == "TEMPORAL"
    # Never rewritten as current.
    assert qualified[0].temporal_state == "CURRENT_STATE"
    assert "past" in qualified[0].limitation.lower() or "published" in qualified[0].limitation.lower()


# ------------------------------------------------------------------------- conflict


def test_conflicting_evidence_is_shown_without_silently_picking_a_winner():
    proposition = _prop("injury status", info="INJURY")
    goal, requirement_id = _goal_for(proposition)
    doc_a, span_a, find_a = _verify_pair(
        proposition, "Shohei Ohtani injury status: he is injured.", "2026-10-01",
        _role_llm(source_class="NEWS", content_role="REPORTING", evidential_basis="SECONDARY"),
    )
    doc_b, span_b, find_b = _verify_pair(
        proposition, "Shohei Ohtani injury status: he is not injured.", "2026-10-01",
        _role_llm(source_class="NEWS", content_role="REPORTING", evidential_basis="SECONDARY",
                  relation="CONTRADICTS", strength="ASSERTED", status="CONTRADICTED"),
    )
    assert find_a.status == VerificationStatus.SUPPORTED
    assert find_b.status == VerificationStatus.CONTRADICTED
    artifact = _multi_artifact(goal, proposition, [(doc_a, span_a, find_a), (doc_b, span_b, find_b)])
    projection = _judge(goal, artifact, requirement_id)
    updated = next(p for p in projection.propositions if p.id == proposition.id)
    assert updated.coverage in {CoverageState.PARTIAL, CoverageState.CONTRADICTED}
    assert not [c for c in projection.claims if proposition.id in c.proposition_ids
                and c.comparison_support is not None]
    contradicted = [q for q in projection.qualified_evidence if q.kind == "CONTRADICTED"]
    assert contradicted, "the conflicting source is preserved rather than silently dropped"


# -------------------------------------------------------------- hidden evidence


def test_irrelevant_and_hard_invalid_findings_stay_hidden():
    proposition = _prop("injury status", info="INJURY")
    goal, requirement_id = _goal_for(proposition)
    # A completely off-topic span (no subject / required relationship) never qualifies.
    doc_a, span_a, find_a = _verify_pair(
        proposition, "The weather in Boston was sunny.", "2026-10-01",
        _role_llm(source_class="NEWS", content_role="REPORTING", evidential_basis="SECONDARY"),
    )
    assert find_a.status in {VerificationStatus.INSUFFICIENT, VerificationStatus.IRRELEVANT}
    artifact = _multi_artifact(goal, proposition, [(doc_a, span_a, find_a)])
    projection = _judge(goal, artifact, requirement_id)
    assert not projection.qualified_evidence
    assert not projection.claims


# ------------------------------------------------------------------- composer text


def test_qualified_evidence_is_rendered_with_its_limitation_by_the_fallback_composer():
    proposition = _prop("roster status", info="INJURY")
    text = "Shohei Ohtani roster status: a community post says he was optioned today."
    llm = _role_llm(source_class="COMMUNITY", content_role="FIRSTHAND", evidential_basis="UNKNOWN")
    projection = _end_to_end(proposition, text, llm)
    assert projection.qualified_evidence
    answer = ResponseComposer(None).compose(
        goal=_goal_for(proposition)[0], semantic=SemanticInterpretation(raw_query="status"),
        projection=projection,
    )
    assert "optioned" in answer
    assert "not established" in answer.lower() or "未能确认" in answer


# ------------------------------------------------------- partial comparison operand


def test_multi_part_requirement_communicates_approved_qualified_and_missing():
    from app.contracts.models import (
        AnswerProposition, AuthorityExpectation, AuthorityNeed, PropositionKind,
        RequirementKind, UserRequirement,
    )
    from app.verification.evidence_semantics import derive_assertion_expectation, derive_temporal_expectation
    from app.verification.judge import Judge

    def build(pid: str, rid: str, required: str, info: str) -> AnswerProposition:
        p = AnswerProposition(id=pid, requirement_id=rid, statement=f"Shohei Ohtani {required}",
                              kind=PropositionKind.FACT, subject="Shohei Ohtani",
                              required_information=required, time_scope={}, information_class=info)
        p.authority_expectation = AuthorityExpectation(need=AuthorityNeed.OFFICIAL_STATE, required=True)
        p.temporal_expectation = derive_temporal_expectation(p)
        p.assertion_expectation = derive_assertion_expectation(p)
        return p

    team = build("p_team", "r_team", "current team", "TRANSACTION")
    injury = build("p_injury", "r_injury", "injury status", "INJURY")
    contract = build("p_contract", "r_contract", "contract status", "TRANSACTION")
    reqs = [
        UserRequirement(id="r_team", kind=RequirementKind.EVIDENCE, statement="current team",
                        value={"objective": "current team"}),
        UserRequirement(id="r_injury", kind=RequirementKind.EVIDENCE, statement="injury status",
                        value={"objective": "injury status"}),
        UserRequirement(id="r_contract", kind=RequirementKind.EVIDENCE, statement="contract status",
                        value={"objective": "contract status"}),
    ]
    goal = Goal(statement="multi", raw_query="multi", requirements=reqs)
    goal.propositions = [team, injury, contract]

    def artifact_for(proposition, text, llm):
        document = _document(text, "2026-10-01")
        span = _span(text, document)
        finding = verify_span(proposition, span, document, llm=llm, allow_llm=True)
        return _web_artifact(goal, proposition, document, span, finding)

    team_art = artifact_for(
        team, "Shohei Ohtani current team: the club announcement lists the Dodgers.",
        _role_llm(source_class="FIRST_PARTY", content_role="OFFICIAL_STATEMENT", evidential_basis="PRIMARY"),
    )
    injury_art = artifact_for(
        injury, "Shohei Ohtani injury status: a community post says he is hurt.",
        _role_llm(source_class="COMMUNITY", content_role="FIRSTHAND", evidential_basis="UNKNOWN"),
    )
    projection = Judge().evaluate(goal, [team_art, injury_art], [])

    assert projection.goal_status == GoalStatus.LIMITED
    assert [c for c in projection.claims if "p_team" in c.proposition_ids]
    assert [q for q in projection.qualified_evidence if q.proposition_id == "p_injury"]
    assert any("contract" in lim.lower() for lim in projection.limitations)
    answer = ResponseComposer(None).compose(goal=goal, semantic=SemanticInterpretation(raw_query="multi"), projection=projection)
    assert "Dodgers" in answer
    assert "community post" in answer.lower()
    assert "contract" in answer.lower()


def test_partial_comparison_operand_remains_visible_without_a_comparison_claim():
    from app.contracts.models import (
        AnswerProposition, ComparativeRelation, ComparisonContract, ComparisonOperand,
        PropositionKind, RequirementKind, UserRequirement,
    )
    from tests.m212_receipt_fixture import with_receipt
    from app.contracts.models import Artifact as _A, PlannerNeed, Scope
    from app.verification.judge import Judge

    reqs, artifacts, needs, sides = [], [], [], []
    for side, season, value in (("lhs", 2025, 0.312), ("rhs", 2026, 0.301)):
        mention = f"Player {side}"
        e = UserRequirement(id=f"e_{side}", kind=RequirementKind.ENTITY, statement=mention,
                            value={"mention": mention, "role": "BATTER"})
        s = UserRequirement(id=f"s_{side}", kind=RequirementKind.SEASON, statement=str(season), value=season)
        m = UserRequirement(id=f"m_{side}", kind=RequirementKind.MEASURE, statement="avg",
                            value={"key": "avg", "label": "avg"})
        clause = f"Report {mention} batter avg in {season}"
        a = UserRequirement(id=f"a_{side}", kind=RequirementKind.ANALYSIS, statement=clause,
                            value={"objective": clause}, original_intent=clause, bound_source_clause=clause)
        reqs.extend((e, s, m, a))
        sides.append(ComparisonOperand(requirement_ids=(e.id, s.id, m.id, a.id), measure_requirement_id=m.id))
        artifacts.append(_A(id=f"identity_{side}", kind="ENTITY_RESOLUTION",
                            structured_data={"resolved": [{"mention": mention, "name": mention,
                                                            "id": 1, "role": "BATTER"}]}))
        artifacts.append(with_receipt(_A(id=f"result_{side}", kind="DATABASE_RESULT", status="ACCEPTED",
            need_id=f"need_{side}", structured_data={"rows": [{"avg": value}], "row_count": 1},
            text_content=f"{season} avg: {value}", actual_scope=Scope(entities=["1"], season=season, measures=["avg"]))))
        needs.append(PlannerNeed(id=f"need_{side}", objective=clause, capability="local_analytics",
                                 requires=[a.id, m.id]))
    comp = ComparisonContract(lhs=sides[0], rhs=sides[1], relation=ComparativeRelation.GT)
    reqs.append(UserRequirement(id="comparison", kind=RequirementKind.ANALYSIS,
                                statement="compare the two typed operands",
                                value={"objective": "compare the two typed operands"}, comparison=comp))
    goal = Goal(statement="compare", raw_query="compare", requirements=reqs)
    # Drop the rhs result: only the 2025 operand is verified.
    artifacts = [a for a in artifacts if a.id != "result_rhs"]
    projection = Judge().evaluate(goal, artifacts, needs)
    comparison_prop = next(p for p in projection.propositions if p.kind == PropositionKind.COMPARISON)
    assert comparison_prop.coverage != CoverageState.SATISFIED
    assert not [c for c in projection.claims if comparison_prop.id in c.proposition_ids
                and c.comparison_support is not None], "no comparative conclusion"
    assert projection.goal_status != GoalStatus.COMPLETE
    operand = [q for q in projection.qualified_evidence if q.kind == "PARTIAL_OPERAND"]
    assert operand, "the verified operand must remain visible"
    assert "0.312" in operand[0].span_text
    assert "comparison" in operand[0].limitation.lower()
