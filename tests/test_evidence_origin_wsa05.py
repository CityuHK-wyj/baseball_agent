"""Fresh WSA-05 mechanism/adversarial/metamorphic corpus; no vocabulary in production."""
import json
from itertools import permutations
from types import SimpleNamespace

import pytest

from app.contracts.evidence_origin import CorroborationState as CS, DependencyState as DS, OriginLineage
from app.contracts.models import (
    AnswerProposition, EvidenceFinding, EvidenceSpan, RetrievedDocument, VerificationStatus,
    SemanticInterpretation, AnswerProjection, GoalStatus,
)
from app.verification.evidence_origin import resolve_origins, origin_presentation, MAX_ORIGIN_ITEMS
from app.verification.lineage import text_hash, proposition_signature
from app.verification.judge import Judge
from app.response.composer import ResponseComposer
from app.response.composition_contract import parse_composer_draft, parse_conformance_assessment
from app.response.answer_contract import AnswerPackage, AnswerFact
from app.verification.composition_conformance import conform_composition


def prop(pid="p", statement="Kai Mori signing transaction"):
    return AnswerProposition(id=pid, requirement_id="r", statement=statement,
                             subject="Kai Mori", required_information="signing transaction")


def evidence(p, texts, *, lineage=None, canonical=None, relation=None):
    findings, docs, spans = [], {}, {}
    for i, text in enumerate(texts):
        doc = RetrievedDocument(id=f"d{i}", url=f"https://publisher-{i}.test/story",
            canonical_url=(canonical or {}).get(i, ""), title=f"Outlet {i}", provider=f"provider-{i}",
            body_text=text, content_hash=text_hash(text), retrieved_at="2026-10-09T00:00:00Z")
        span = EvidenceSpan(id=f"s{i}", document_id=doc.id, text=text, start=0, end=len(text),
                            origin_lineage=OriginLineage.model_validate((lineage or {}).get(i, {})))
        if (lineage or {}).get(i):
            span.origin_lineage.proposition_signature = proposition_signature(p)
        finding = EvidenceFinding(id=f"e{i}", document_id=doc.id, span_id=span.id,
            proposition_id=p.id, proposition_signature=proposition_signature(p), span_hash=text_hash(text),
            admissible=True, status=VerificationStatus.SUPPORTED, deterministic_status=VerificationStatus.SUPPORTED,
            relation_state=(relation or {}).get(i, "ENTAILS"), what_it_establishes=text,
            checks={"subject": True, "target_ok": True},
            semantic_assessment={"source_role": {"source_class": "NEWS", "content_role": "REPORTING"}})
        findings.append(finding)
        docs[doc.id], spans[span.id] = doc, span
    return findings, docs, spans


class OriginLLM:
    configured = True
    def __init__(self, relations=None, error=False):
        self.relations, self.error, self.calls = relations or [], error, []
    def complete_json(self, **kwargs):
        self.calls.append(json.loads(kwargs["user"]))
        if self.error:
            raise RuntimeError("unavailable")
        return SimpleNamespace(data={"relations": self.relations})


def proposal(a, b, state, texts, kind="EXPLICIT_ATTRIBUTION"):
    return {"evidence_id": f"e{a}", "related_evidence_id": f"e{b}", "dependency_state": state,
            "basis_description": "Grounded basis relationship", "positive_basis_kind": kind,
            "evidence_quotes": {f"e{a}": texts[a], f"e{b}": texts[b]}}


def resolve(texts, *, p=None, llm=None, **kwargs):
    p = p or prop()
    values = evidence(p, texts, **kwargs)
    return resolve_origins(p, *values, llm=llm)


def test_A_exact_syndication_one_basis():
    result = resolve(["AP: Kai Mori signed."] * 3)
    assert result.corroboration_state == CS.DEPENDENT_MULTIPLE_DOCUMENTS
    assert len(result.groups) == 1
    assert len(result.groups[0].member_evidence_ids) == 3


def test_B_edited_syndication_structured_article_metadata():
    texts = ["Wire original: Kai Mori signed.", "Shortened copy: Mori signed.", "Local edited copy and context."]
    result = resolve(texts, lineage={
        0: {"basis_source_ids": ["wire:article-920"]},
        1: {"derived_from_basis_ids": ["wire:article-920"]},
        2: {"derived_from_basis_ids": ["wire:article-920"]},
    })
    assert len(result.groups) == 1
    assert any(r.dependency_state == DS.DERIVED_FROM and r.evidence_id == "e1" for r in result.relations)


@pytest.mark.parametrize("attribution", [
    "ESPN reports the club's release: Kai Mori signed.",
    "The move, announced by the club, was summarized in the Ledger.",
    "球団の発表によると、契約が成立した。", "据球队公布的签约公告，合同已经签署。",
])
def test_C_different_domains_same_announcement_interpreted_by_set_assessor(attribution):
    texts = ["Club release: we signed Kai Mori.", attribution, "League summary of the club release."]
    llm = OriginLLM([proposal(i, 0, "DERIVED_FROM", texts) for i in (1, 2)])
    result = resolve(texts, llm=llm)
    assert len(result.groups) == 1
    assert len(llm.calls) == 1
    assert len(llm.calls[0]["evidence"]) == 3


def test_D_explicit_reporter_dependency_direction():
    texts = ["Leila: my source says Kai Mori is close to signing.",
             "Omar: citing Leila's report, Kai Mori is close to signing."]
    result = resolve(texts, llm=OriginLLM([proposal(1, 0, "DERIVED_FROM", texts)]))
    assert result.relations[0].evidence_id == "e1"
    assert result.relations[0].related_evidence_id == "e0"
    assert result.relations[0].dependency_state == DS.DERIVED_FROM


@pytest.mark.parametrize("texts", [
    ["A league source tells Nadia a deal is near.", "Multiple club sources tell Emilio a deal is near."],
    ["Someone familiar with talks says it may happen.", "Our reporting suggests it could happen."],
    ["情報筋によると契約に近い。", "関係者は契約の可能性を示した。"],
])
def test_E_no_dependency_signal_no_independence(texts):
    result = resolve(texts, llm=OriginLLM())
    assert result.corroboration_state == CS.DEPENDENCY_UNKNOWN
    assert all(g.dependency_state == DS.DEPENDENCY_UNKNOWN for g in result.groups)


def test_F_distinct_primary_records_positive_basis():
    texts = ["The signed player contract, document C-17, records the signing.",
             "The league registration record R-41 separately records the signing."]
    result = resolve(texts, llm=OriginLLM([proposal(0, 1, "SEPARATE_EVIDENTIARY_BASIS", texts,
                                                "DISTINCT_PRIMARY_RECORDS")]))
    assert result.corroboration_state == CS.MULTIPLE_SEPARATE_BASES
    assert len(result.groups) == 2


@pytest.mark.parametrize("mutation", ["domain", "wording", "author", "missing", "ungrounded", "foreign_id"])
def test_positive_separate_proof_required(mutation):
    texts = ["Nadia reports a rumor.", "Emilio reports a rumor."]
    raw = proposal(0, 1, "SEPARATE_EVIDENTIARY_BASIS", texts, "DISTINCT_PRIMARY_RECORDS")
    if mutation in {"domain", "wording", "author"}:
        raw["positive_basis_kind"] = mutation.upper()
    elif mutation == "missing":
        raw["evidence_quotes"] = {}
    elif mutation == "ungrounded":
        raw["evidence_quotes"]["e0"] = "a primary record that was not retrieved"
    else:
        raw["related_evidence_id"] = "unretrieved-evidence"
    result = resolve(texts, llm=OriginLLM([raw]))
    assert result.corroboration_state == CS.DEPENDENCY_UNKNOWN
    assert not result.relations


@pytest.mark.parametrize("gate", ["speculative", "stale", "authority"])
def test_G_H_I_separate_bases_never_upgrade_compatibility_or_judge(gate):
    from tests.test_source_authority_wsa03 import _prop, _goal_for, _role_llm
    from tests.test_web_evidence_semantics_wsa01_02 import _web_artifact
    from app.verification.evidence import verify_span
    p = _prop("injury status", info="INJURY")
    texts = ["Shohei Ohtani injury status: source record alpha says he may be injured.",
             "Shohei Ohtani injury status: source record beta says he may be injured."]
    fs, docs, spans = evidence(p, texts)
    assessor = _role_llm(source_class="COMMUNITY" if gate == "authority" else "NEWS",
        content_role="FIRSTHAND" if gate == "authority" else "REPORTING", evidential_basis="SECONDARY",
        strength="SPECULATIVE" if gate == "speculative" else "ASSERTED")
    if gate != "speculative":
        texts = [t.replace("may be", "is") for t in texts]
        fs, docs, spans = evidence(p, texts)
    goal, _ = _goal_for(p)
    artifacts = []
    for i in range(2):
        doc, span = docs[f"d{i}"], spans[f"s{i}"]
        doc.publication_date = "2019-01-01" if gate == "stale" else "2026-10-01"
        finding = verify_span(p, span, doc, llm=assessor)
        assert finding.status != VerificationStatus.SUPPORTED
        artifacts.append(_web_artifact(goal, p, doc, span, finding))
    # This positively separate origin fixture is deliberately maximally favorable: even
    # then, the actual verifier's assertion/time/authority gates must remain authoritative.
    ids = [a.structured_data["evidence_findings"][0]["id"] for a in artifacts]
    raw = proposal(0, 1, "SEPARATE_EVIDENTIARY_BASIS", texts, "DISTINCT_PRIMARY_RECORDS")
    raw.update(evidence_id=ids[0], related_evidence_id=ids[1], evidence_quotes=dict(zip(ids, texts)))
    projection = Judge(llm=OriginLLM([raw]), max_inspections=0).evaluate(goal, artifacts, [])
    assert projection.goal_status != GoalStatus.COMPLETE
    assert not projection.claims
    assert len(projection.qualified_evidence) == 2
    assert projection.evidence_origins[0].corroboration_state == CS.MULTIPLE_SEPARATE_BASES


def test_J_support_contradiction_conflict_no_voting():
    texts = [f"Different reporting record {i}" for i in range(7)]
    result = resolve(texts, relation={6: "CONTRADICTS"})
    assert result.corroboration_state == CS.CONFLICTING_BASES
    assert len(result.groups) == 7
    assert "does not decide" in origin_presentation([result])


def test_K_same_document_different_propositions_span_lineage_local():
    p1, p2 = prop(), prop("value", "Is the contract good value?")
    texts = ["Club primary signing release.", "Article quotes club signing release. Author's analysis: good value."]
    fs, docs, spans = evidence(p1, texts, lineage={
        0: {"basis_source_ids": ["club:release-9"]}, 1: {"derived_from_basis_ids": ["club:release-9"]}})
    result1 = resolve_origins(p1, fs, docs, spans)
    assert len(result1.groups) == 1
    # Different span of the SAME article: new source contribution, no derivative metadata.
    analysis = "Author's analysis: good value."
    spans["s1"].text = analysis
    spans["s1"].start = docs["d1"].body_text.index(analysis)
    spans["s1"].end = len(docs["d1"].body_text)
    spans["s1"].origin_lineage = OriginLineage(basis_source_ids=["writer:analysis-1"])
    for f in fs:
        f.proposition_id, f.proposition_signature = p2.id, proposition_signature(p2)
        f.span_hash = text_hash(spans[f.span_id].text)
    result2 = resolve_origins(p2, fs, docs, spans)
    assert len(result2.groups) == 2
    assert not result2.relations
    assert result2.corroboration_state == CS.DEPENDENCY_UNKNOWN


def test_L_unknown_dependency_presentation_keeps_evidence():
    from app.contracts.models import QualifiedEvidence, Goal
    result = resolve(["A says Kai Mori may sign.", "B says he may sign."])
    projection = AnswerProjection(goal_id="g", goal_status=GoalStatus.LIMITED,
        evidence_origins=[result], qualified_evidence=[QualifiedEvidence(
            span_text="The edited report adds useful contract context.", limitation="speculative, not confirmed")])
    answer = ResponseComposer(None).compose(Goal(id="g", statement="rumor", raw_query="rumor"), SemanticInterpretation(raw_query="rumor", language="en"), projection)
    assert "useful contract context" in answer
    assert "speculative, not confirmed" in answer
    assert "could not establish" in answer


@pytest.mark.parametrize("identity", ["canonical", "record", "basis"])
def test_closed_world_dependency_identity(identity):
    p = prop()
    fs, docs, spans = evidence(p, ["original rendering", "different rendering"])
    if identity == "canonical":
        for d in docs.values():
            d.canonical_url = "https://wire.test/exact-article"
    elif identity == "record":
        for s in spans.values():
            s.location = "structured:source-record-217"
    else:
        for s in spans.values():
            s.origin_lineage.basis_source_ids = ["wire:record-217"]
            s.origin_lineage.proposition_signature = proposition_signature(p)
    llm = OriginLLM(error=True)
    result = resolve_origins(p, fs, docs, spans, llm=llm)
    assert len(result.groups) == 1
    assert result.relations[0].method == "deterministic"
    assert not llm.calls


def test_common_provider_identifier_is_not_same_record():
    result = resolve(["unrelated first report", "unrelated second report"], lineage={
        0: {"basis_source_ids": ["AP"]}, 1: {"basis_source_ids": ["AP"]}})
    assert result.corroboration_state == CS.DEPENDENCY_UNKNOWN


def test_no_extra_fetch_singleton_and_cross_proposition_not_assessed():
    p = prop()
    fs, docs, spans = evidence(p, ["A", "B"])
    fs[1].proposition_id = "other"
    llm = OriginLLM()
    assert resolve_origins(p, fs, docs, spans, llm=llm) is None
    assert not llm.calls


@pytest.mark.parametrize("failure", ["provider", "malformed", "unresolved_span", "stale_signature"])
def test_unavailable_or_invalid_inputs_never_produce_independence(failure):
    p = prop()
    fs, docs, spans = evidence(p, ["report one", "report two"])
    llm = OriginLLM(error=failure == "provider")
    if failure == "malformed":
        llm.relations = [None, {}, {"evidence_id": [], "dependency_state": "INDEPENDENT"}]
    elif failure == "unresolved_span":
        spans["s1"].text = "fabricated"
    elif failure == "stale_signature":
        fs[1].proposition_signature = "old"
    result = resolve_origins(p, fs, docs, spans, llm=llm)
    assert result is None or result.corroboration_state == CS.DEPENDENCY_UNKNOWN


def test_set_cap_keeps_unassessed_evidence_unknown_not_hidden():
    texts = [f"Different firsthand record {i}" for i in range(MAX_ORIGIN_ITEMS + 4)]
    llm = OriginLLM()
    result = resolve(texts, llm=llm)
    assert len(llm.calls) == 1
    assert len(llm.calls[0]["evidence"]) == MAX_ORIGIN_ITEMS
    assert sum(len(g.member_evidence_ids) for g in result.groups) == len(texts)
    assert any("bounded" in s for s in result.limitations)


def test_fresh_metamorphic_order_names_domains_languages_and_duplicate_counts():
    # 6 permutations for 3-item prefixes, 2 for 2-item prefixes: 80 fresh sets.
    for names in [("North", "South"), ("Folio", "Gazette"), ("日報", "通信"), ("日报", "周刊")]:
        for copies in [1, 2, 5, 9]:
            texts = [f"{names[0]} primary announcement"] + [f"{names[1]} edited version {i}" for i in range(copies)]
            lineage = {0: {"basis_source_ids": [f"{names[0]}:announcement-unique"]}}
            lineage.update({i: {"derived_from_basis_ids": [f"{names[0]}:announcement-unique"]} for i in range(1, copies + 1)})
            p = prop()
            fs, docs, spans = evidence(p, texts, lineage=lineage)
            for order in permutations(range(min(3, len(fs)))):
                reordered = [fs[i] for i in order] + fs[3:]
                if len(reordered) != len(fs):
                    continue
                result = resolve_origins(p, reordered, docs, spans)
                assert len(result.groups) == 1
                assert result.corroboration_state == CS.DEPENDENT_MULTIPLE_DOCUMENTS
            # Removing all positive lineage must NOT convert copies into separate bases.
            for span in spans.values():
                span.origin_lineage = OriginLineage()
            unknown = resolve_origins(p, fs, docs, spans)
            assert unknown.corroboration_state == CS.DEPENDENCY_UNKNOWN


@pytest.mark.parametrize("kind", ["FACTUAL", "QUALIFIED", "ANALYSIS", "LIMITATION"])
def test_composer_cannot_invent_origin_in_any_statement_kind(kind):
    package = AnswerPackage(goal_id="g", goal_status="LIMITED", language="en",
        facts=[AnswerFact(marker="S1", text="One report exists.", claim_id="c")],
        evidence_origins=[resolve(["first report", "second report"])])
    result = parse_composer_draft({"statements": [{"kind": kind,
        "text": "Two independent sources confirm the signing. [S1]", "citations": ["S1"]}]})
    from tests.conformance_fixtures import bound_review
    verdict = bound_review({"statements": [{"index": 0,
        "epistemic": kind, "origin_claim": True, "origin_backed": False}]}, package, result)
    answer = conform_composition(result, package, verdict)
    assert "Two independent sources confirm" not in answer.text
    assert "ORIGIN_OVERCLAIM" in {v.kind for v in answer.violations}
    assert "could not establish" in answer.text
    assert "One report exists." in answer.text


def test_positive_origin_explanation_not_misrepresented_as_source_citation():
    origins = resolve(["AP same article"] * 2)
    package = AnswerPackage(goal_id="g", goal_status="COMPLETE", language="en", evidence_origins=[origins])
    result = parse_composer_draft({"statements": [{"kind": "FACTUAL", "text": "These reports share a basis. [S1]"}]})
    from tests.conformance_fixtures import bound_review
    verdict = bound_review({"statements": [{"index": 0, "epistemic": "FACTUAL",
                                                            "origin_claim": True, "origin_backed": True}]}, package, result)
    answer = conform_composition(result, package, verdict).text
    assert "share a basis" in answer
    assert "[S1]" not in answer


def test_partial_separate_plus_unknown_is_mixed_not_universal_independence():
    texts = ["Primary document A", "Primary document B", "unclear reporting C"]
    result = resolve(texts, llm=OriginLLM([proposal(0, 1, "SEPARATE_EVIDENTIARY_BASIS", texts,
                                                "DISTINCT_PRIMARY_DOCUMENTS")]))
    assert result.corroboration_state == CS.MIXED
    assert "could not establish" in origin_presentation([result])


def test_derived_evidence_remains_in_original_governed_inputs():
    p = prop()
    fs, docs, spans = evidence(p, ["Official record", "Derivative report with extra context"], lineage={
        0: {"basis_source_ids": ["official:record"]}, 1: {"derived_from_basis_ids": ["official:record"]}})
    before = [f.model_dump() for f in fs]
    result = resolve_origins(p, fs, docs, spans)
    assert len(result.groups) == 1
    assert [f.model_dump() for f in fs] == before
    assert "extra context" in spans["s1"].text
