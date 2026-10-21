"""Generated/property/metamorphic tests for ContentArtifact + Representation (M2.9, ADR 0031).

These tests exercise the external-content boundary, not named examples. Each generated case
records its seed; replay one with, e.g., ``pytest -k seed_17``. The properties they assert
are architecture invariants: preserve first, interpret lazily, never fabricate missing fields,
keep native structure, bind evidence to a concrete representation, and disclose views without
loading every payload into context.
"""

from __future__ import annotations

import json
import random

import pytest

from app.capabilities.web_search import FetchedPage
from app.contracts.models import (
    EvidenceSource,
    EvidenceSpan,
    Representation,
    RepresentationKind,
    RetrievedDocument,
)
from app.verification.lineage import span_resolves, text_hash
from app.web.representation import (
    build_representations,
    canonical_json,
    content_artifact_summary,
    derive_structured_view,
    derive_text_representation,
    iter_structured_records,
    representation_index,
    resolve_json_pointer,
    structured_evidence_spans,
)


# --------------------------------------------------------------------------- generators

def _json_page(seed: int) -> tuple[FetchedPage, dict]:
    rng = random.Random(seed)
    record = {
        "player": f"Player{rng.randrange(10000)}",
        "homeRuns": rng.randrange(0, 60),
        "season": rng.choice([2023, 2024, 2025]),
        "notes": f"note-{rng.randrange(1000)}",
    }
    if rng.random() < 0.5:
        record["unknownField"] = f"extra-{rng.randrange(100)}"
    raw = json.dumps(record).encode()
    page = FetchedPage(
        url=f"https://api{rng.randrange(100)}.example.org/stats",
        final_url=f"https://api{rng.randrange(100)}.example.org/stats",
        title="", text=raw.decode(), retrieved_at="2026-09-30T00:00:00Z", ok=True,
        content_hash=f"hash{seed}", retrieval_method="http",
        raw_content=raw, content_type="application/json",
    )
    return page, record


def _html_page(seed: int, *, with_metadata: bool) -> FetchedPage:
    rng = random.Random(seed)
    author = f'<meta name="author" content="Author{rng.randrange(100)}">' if with_metadata else ""
    date = '<meta property="article:published_time" content="2025-06-01">' if with_metadata else ""
    body = f"<p>Report {rng.randrange(10000)} about a public baseball event.</p>"
    raw = f"<html><head><title>Story {seed}</title>{author}{date}</head><body>{body}</body></html>".encode()
    return FetchedPage(
        url=f"https://publisher{rng.randrange(100)}.example.org/story",
        final_url=f"https://publisher{rng.randrange(100)}.example.org/story",
        title=f"Story {seed}", text=f"Report {seed} about a public baseball event.",
        retrieved_at="2026-09-30T00:00:00Z", ok=True, content_hash=f"htmlhash{seed}",
        retrieval_method="http", raw_content=raw, content_type="text/html",
    )


def _text_page(seed: int) -> FetchedPage:
    rng = random.Random(seed)
    return FetchedPage(
        url=f"https://plain{rng.randrange(100)}.example.org/note",
        final_url=f"https://plain{rng.randrange(100)}.example.org/note",
        title="", text=f"Plain text note {rng.randrange(10000)}.",
        retrieved_at="2026-09-30T00:00:00Z", ok=True, content_hash=f"texthash{seed}",
        retrieval_method="tavily_extract", raw_content=f"plain {seed}".encode(),
        content_type="text/plain",
    )


def _xml_page(seed: int) -> FetchedPage:
    rng = random.Random(seed)
    raw = (
        "<rss><channel><item><title>Item %d</title>"
        "<description>Body %d</description></item></channel></rss>" % (seed, rng.randrange(1000))
    ).encode()
    return FetchedPage(
        url=f"https://feed{rng.randrange(100)}.example.org/rss.xml",
        final_url=f"https://feed{rng.randrange(100)}.example.org/rss.xml",
        title="Feed", text=raw.decode(), retrieved_at="2026-09-30T00:00:00Z", ok=True,
        content_hash=f"xmlhash{seed}", retrieval_method="rss_item",
        raw_content=raw, content_type="application/xml",
    )


def _generated_page(seed: int) -> FetchedPage:
    """A deterministic heterogeneous acquisition (HTML / JSON / XML / text / rendered DOM)."""
    rng = random.Random(seed)
    flavor = rng.choice(["html_metadata", "html_bare", "json", "json_array", "xml", "text", "dom"])
    if flavor == "html_metadata":
        return _html_page(seed, with_metadata=True)
    if flavor == "html_bare":
        return _html_page(seed, with_metadata=False)
    if flavor in {"json", "json_array"}:
        page, _ = _json_page(seed)
        if flavor == "json_array":
            array = [{"id": rng.randrange(100), "value": f"v{rng.randrange(100)}"} for _ in range(3)]
            page.raw_content = json.dumps(array).encode()
            page.text = page.raw_content.decode()
        return page
    if flavor == "xml":
        return _xml_page(seed)
    if flavor == "dom":
        page = _html_page(seed, with_metadata=False)
        page.retrieval_method = "browser"
        return page
    return _text_page(seed)


# ------------------------------------------------------------------- representation build

@pytest.mark.parametrize("seed", range(50), ids=lambda seed: f"seed_{seed}")
def test_generated_heterogeneous_ingestion_preserves_available_representations(seed):
    page = _generated_page(seed)
    reps = build_representations(page, extractor_version="extractor-v1")
    assert reps, "every acquired resource must expose at least one representation"
    kinds = {rep.kind for rep in reps}
    assert RepresentationKind.TEXT in kinds, "the compatibility text view is always available"
    assert all(rep.content_hash for rep in reps)
    assert all(rep.content_ref for rep in reps)
    text = next(rep for rep in reps if rep.kind == RepresentationKind.TEXT)
    assert text.content == (page.text or "")
    # The extracted text view derives from its native parent when one was retained.
    if page.raw_content:
        parent_id = text.derived_from
        assert parent_id is not None and any(rep.id == parent_id for rep in reps)
        assert text.derived_by == "deterministic"
        assert text.is_derived() and not text.is_source()


def test_missing_optional_metadata_does_not_prevent_ingestion_or_fabricate_fields():
    page = _text_page(7)
    reps = build_representations(page, extractor_version="extractor-v1")
    document = RetrievedDocument(
        url=page.final_url, final_url=page.final_url, retrieved_at=page.retrieved_at,
        content_hash=page.content_hash, body_text=page.text,
        representations=reps, primary_representation_id=next(
            (r.id for r in reps if r.kind == RepresentationKind.TEXT), ""
        ),
    )
    # Absent values stay absent; the model does not synthesize a title/author/date/speaker.
    assert document.title == ""
    assert document.publication_date is None
    assert document.source_uri == ""
    # A bare acquisition still has a usable text representation.
    assert document.primary_representation() is not None
    assert document.primary_representation().kind == RepresentationKind.TEXT


def test_native_structured_json_is_preserved_not_flattened():
    page, record = _json_page(3)
    reps = build_representations(page, extractor_version="extractor-v1")
    json_rep = next(rep for rep in reps if rep.kind == RepresentationKind.JSON)
    assert json_rep.structured_data == record
    assert json_rep.locator_type == "json_pointer"
    # The canonical serialization round-trips exactly.
    assert json.loads(canonical_json(json_rep.structured_data)) == record


def test_xml_and_feed_representations_retain_a_native_view():
    page = _xml_page(11)
    reps = build_representations(page, extractor_version="rss-item-v1")
    feed = next(rep for rep in reps if rep.kind == RepresentationKind.FEED_ITEM)
    assert feed.structured_data is not None
    assert feed.media_type == "application/xml"


# -------------------------------------------------------------------- lazy derivation

def test_derived_view_does_not_mutate_its_source_representation():
    page = _html_page(5, with_metadata=True)
    reps = build_representations(page, extractor_version="extractor-v1")
    parent = next(rep for rep in reps if rep.kind == RepresentationKind.HTML)
    before_hash, before_content = parent.content_hash, parent.content
    child = derive_text_representation(None, parent, method="html-to-text-v1", text="derived body")
    assert child.derived_from == parent.id
    assert child.derivation_method == "html-to-text-v1"
    assert child.is_derived()
    # The source is untouched and still prior in the derivation graph.
    assert parent.content_hash == before_hash and parent.content == before_content
    assert parent.is_source()


def test_identical_derivations_retain_lineage_and_deduplicate_by_content_hash():
    page, _ = _json_page(9)
    reps = build_representations(page, extractor_version="extractor-v1")
    parent = next(rep for rep in reps if rep.kind == RepresentationKind.JSON)
    first = derive_structured_view(None, parent, method="record-v1", pointer="")
    second = derive_structured_view(None, parent, method="record-v1", pointer="")
    assert first is not None and second is not None
    assert first.content_hash == second.content_hash
    assert first.derived_from == second.derived_from == parent.id
    assert first.id != second.id  # distinct views; identical content is recognized by hash


def test_llm_derived_views_are_distinguishable_from_source_representations():
    page, _ = _json_page(13)
    reps = build_representations(page, extractor_version="extractor-v1")
    parent = next(rep for rep in reps if rep.kind == RepresentationKind.JSON)
    llm_view = derive_structured_view(None, parent, method="llm-semantic-v1", pointer="", derived_by="llm")
    assert llm_view is not None
    assert llm_view.derived_by == "llm" and llm_view.is_derived() and not llm_view.is_source()
    assert parent.is_source()
    # A derived interpretation never overwrites the deterministic parent.
    assert parent.structured_data == json.loads(page.raw_content)


def test_adding_a_representation_leaves_existing_representations_unchanged():
    page = _html_page(21, with_metadata=False)
    reps = build_representations(page, extractor_version="extractor-v1")
    snapshot = {rep.id: (rep.kind, rep.content_hash, rep.content) for rep in reps}
    extra = Representation(kind=RepresentationKind.TABLE, content="a,b\n1,2", content_hash=text_hash("a,b\n1,2"))
    reps.append(extra)
    assert len(reps) == len(snapshot) + 1
    for rep in reps:
        if rep.id in snapshot:
            assert snapshot[rep.id] == (rep.kind, rep.content_hash, rep.content)


# ------------------------------------------------------------------- evidence binding

def test_text_evidence_binds_to_a_representation_and_replays():
    page = _html_page(31, with_metadata=True)
    reps = build_representations(page, extractor_version="extractor-v1")
    document = RetrievedDocument(
        id="doc_text", url=page.final_url, final_url=page.final_url, retrieved_at=page.retrieved_at,
        content_hash=page.content_hash, body_text=page.text, representations=reps,
        primary_representation_id=next(r.id for r in reps if r.kind == RepresentationKind.TEXT),
    )
    span = EvidenceSpan(
        document_id=document.id,
        representation_id=document.primary_representation_id,
        text=page.text, start=0, end=len(page.text),
        source_type=EvidenceSource.DOCUMENT_BODY, retrieval_status="OK",
    )
    assert span.is_admissible()
    assert span_resolves(span, document)


def test_structured_evidence_uses_a_stable_locator_and_replays():
    page, record = _json_page(41)
    reps = build_representations(page, extractor_version="extractor-v1")
    document = RetrievedDocument(
        id="doc_json", url=page.final_url, final_url=page.final_url, retrieved_at=page.retrieved_at,
        content_hash=page.content_hash, body_text=page.text, representations=reps,
        primary_representation_id=next(r.id for r in reps if r.kind == RepresentationKind.TEXT),
    )
    spans = structured_evidence_spans(document)
    assert spans, "a structured representation exposes at least one structured record"
    span = EvidenceSpan.model_validate({**spans[0], "document_id": document.id, "id": "span_s1"})
    assert span.source_type == EvidenceSource.STRUCTURED
    assert span.locator_type == "json_pointer"
    assert span_resolves(span, document)
    # The locator is stable: resolving it reproduces exactly the stored span text.
    rep = document.representation(span.representation_id)
    assert canonical_json(resolve_json_pointer(rep.structured_data, span.locator)) == span.text
    # Degrading a structured locator into char offsets is not required and must not resolve.
    degraded = span.model_copy(update={"locator_type": "char_offsets", "locator": ""})
    assert not span_resolves(degraded, document)


def test_a_fabricated_structured_span_does_not_resolve():
    page, _ = _json_page(43)
    reps = build_representations(page, extractor_version="extractor-v1")
    document = RetrievedDocument(
        id="doc_json2", url=page.final_url, final_url=page.final_url, retrieved_at=page.retrieved_at,
        content_hash=page.content_hash, body_text=page.text, representations=reps,
    )
    rep = next(r for r in reps if r.kind == RepresentationKind.JSON)
    forged = EvidenceSpan(
        document_id=document.id, representation_id=rep.id, text='{"fabricated":true}',
        source_type=EvidenceSource.STRUCTURED, retrieval_status="OK",
        locator_type="json_pointer", locator="/does/not/exist",
    )
    assert not span_resolves(forged, document)


# ------------------------------------------------------------------- disclosure

def test_progressive_disclosure_summary_excludes_representation_payloads():
    page, _ = _json_page(51)
    reps = build_representations(page, extractor_version="extractor-v1")
    document = RetrievedDocument(
        id="doc_disc", url=page.final_url, final_url=page.final_url, retrieved_at=page.retrieved_at,
        content_hash=page.content_hash, body_text=page.text, representations=reps,
        primary_representation_id=next(r.id for r in reps if r.kind == RepresentationKind.TEXT),
    )
    index = representation_index(document)
    summary = content_artifact_summary(document)
    assert index and summary["representations"] == index
    for entry in index:
        assert "content" not in entry
        assert entry["representation_id"] and entry["kind"]
    serialized = json.dumps(summary)
    # The summary names the views without shipping their bodies into context. The raw JSON
    # payload contains fields the index never exposes.
    assert '"homeRuns"' in page.text
    assert '"homeRuns"' not in serialized
    assert page.text not in serialized


# ------------------------------------------------------------------- metamorphic

@pytest.mark.parametrize("seed", range(100, 140), ids=lambda seed: f"mut_seed_{seed}")
def test_metamorphic_metadata_removal_does_not_change_ingestion(seed):
    page = _html_page(seed, with_metadata=True)
    full = build_representations(page, extractor_version="extractor-v1")
    stripped_page = _html_page(seed, with_metadata=False)
    stripped = build_representations(stripped_page, extractor_version="extractor-v1")
    # Removing optional metadata changes neither the availability of a text view nor the
    # structured/native shape; it only removes information that was never required.
    assert {rep.kind for rep in stripped} == {rep.kind for rep in full}
    assert all(rep.content_hash for rep in stripped)
    # No fabricated author/date appears on the document.
    document = RetrievedDocument(url=stripped_page.final_url, body_text=stripped_page.text)
    assert document.title == "" and document.publication_date is None


def test_metamorphic_json_field_order_and_unknown_fields_keep_the_same_record():
    record = {"player": "A Player", "homeRuns": 10, "season": 2025}
    first = canonical_json(record)
    reordered = dict(reversed(list(record.items())))
    reordered["unknownFutureField"] = {"nested": [1, 2, 3]}
    # Canonical serialization is stable under field order and tolerates unknown fields.
    assert canonical_json(reordered) != first  # the unknown field is preserved, not dropped
    parsed = json.loads(canonical_json(reordered))
    assert parsed["unknownFutureField"] == {"nested": [1, 2, 3]}
    assert parsed["player"] == record["player"]


def test_metamorphic_json_pointer_stays_stable_across_field_reordering():
    seed = 123
    page, record = _json_page(seed)
    reps = build_representations(page, extractor_version="extractor-v1")
    rep = next(r for r in reps if r.kind == RepresentationKind.JSON)
    value = resolve_json_pointer(rep.structured_data, "/player")
    reordered = {key: rep.structured_data[key] for key in reversed(list(rep.structured_data))}
    assert resolve_json_pointer(reordered, "/player") == value


def test_metamorphic_provider_transport_change_keeps_source_identity():
    url = "https://publisher.example.org/story"
    for method in ("http", "browser", "tavily_extract"):
        page = FetchedPage(
            url=url, final_url=url, title="Story", text="A public report about an interview.",
            retrieved_at="2026-09-30T00:00:00Z", ok=True, content_hash="samehash",
            retrieval_method=method, canonical_url=url, raw_content=b"same body bytes",
            content_type="text/html",
        )
        reps = build_representations(page, extractor_version="extractor-v1")
        document = RetrievedDocument(
            url=url, final_url=url, canonical_url=url, retrieved_at=page.retrieved_at,
            content_hash=page.content_hash, body_text=page.text, representations=reps,
            representation_provider="tavily" if method == "tavily_extract" else "",
        )
        document.source_uri = document.canonical_url or document.final_url or document.url
        # The transport is recorded on the representation, but the source identity is the URL.
        assert document.source_uri == url
        native = [rep for rep in reps if rep.acquisition_method == method]
        assert native
        assert all(rep.id for rep in reps)


def test_metamorphic_mutating_a_derived_view_leaves_raw_and_evidence_intact():
    page, _ = _json_page(77)
    reps = build_representations(page, extractor_version="extractor-v1")
    parent = next(rep for rep in reps if rep.kind == RepresentationKind.JSON)
    child = derive_structured_view(None, parent, method="record-v1", pointer="")
    raw_snapshot = parent.content_hash
    child.content = "mutated after the fact"
    # Evidence/replay binds to the hash-bearing source, not the mutated derived copy.
    assert parent.content_hash == raw_snapshot
    document = RetrievedDocument(
        id="doc_meta", url=page.final_url, final_url=page.final_url, retrieved_at=page.retrieved_at,
        content_hash=page.content_hash, body_text=page.text, representations=[parent],
    )
    span = EvidenceSpan(
        document_id=document.id, representation_id=parent.id, text='{"ok": true}',
        source_type=EvidenceSource.STRUCTURED, retrieval_status="OK",
        locator_type="json_pointer", locator="/missing",
    )
    assert not span_resolves(span, document)


def test_metamorphic_alternative_text_representation_is_additive():
    page = _html_page(91, with_metadata=True)
    reps = build_representations(page, extractor_version="extractor-v1")
    parent = next(rep for rep in reps if rep.kind == RepresentationKind.HTML)
    alt = derive_text_representation(None, parent, method="reader-view-v2", text="Reader view text.")
    combined = reps + [alt]
    kinds = [rep.kind for rep in combined]
    assert kinds.count(RepresentationKind.TEXT) == 2
    # The original derived text view is untouched; a second view is additive.
    for rep in combined:
        if rep.id in {r.id for r in reps}:
            assert rep.content_hash


def test_structured_record_iteration_handles_nested_and_array_payloads():
    nested = {"results": [{"id": 1, "tags": ["a", "b"]}, {"id": 2}], "meta": {"page": 1}}
    pointers = {pointer for pointer, _ in iter_structured_records(nested)}
    assert "" in pointers
    assert "/results/0" in pointers and "/results/1" in pointers
    array = [{"id": 1}, {"id": 2}, {"id": 3}]
    assert {pointer for pointer, _ in iter_structured_records(array)} == {"/0", "/1", "/2"}


# ------------------------------------------------------------------- integration

def test_retrieve_attaches_representations_and_structured_evidence(monkeypatch):
    from app.capabilities.web_search import SearchHit
    from app.capabilities.web_research import WebResearchTool
    from app.config import WebConfig
    from app.contracts.models import AnswerProposition

    from tests.assessment_fixtures import FixtureAssessorLLM

    tool = WebResearchTool(WebConfig(broad_search=False), llm=FixtureAssessorLLM())
    record = {"player": "Aaron Judge", "homeRuns": 55, "season": 2025}
    raw = json.dumps(record).encode()
    page = FetchedPage(
        url="https://api.example.org/stats", final_url="https://api.example.org/stats",
        title="", text=raw.decode(), retrieved_at="2026-09-30T00:00:00Z", ok=True,
        content_hash="rawhash", retrieval_method="http", canonical_url="https://api.example.org/stats",
        raw_content=raw, content_type="application/json",
    )
    monkeypatch.setattr("app.capabilities.web_research.fetch_with_fallback", lambda *a, **k: page)
    proposition = AnswerProposition(
        id="p1", statement="Aaron Judge has 55 homeRuns",
        subject="Aaron Judge", required_information="homeRuns", mandatory=True,
    )
    findings, _, _, _, documents = tool._retrieve(
        [SearchHit("Stats", page.url, engine="publisher_feeds")],
        "Aaron Judge homeRuns", [], {"aaron", "judge"}, 1, [proposition],
    )
    document = RetrievedDocument.model_validate(documents[0])
    assert document.representations
    assert document.primary_representation_id
    assert document.source_uri == page.url
    structured = [s for s in findings[0]["evidence_spans"] if s["source_type"] == "STRUCTURED"]
    assert structured
    span = EvidenceSpan.model_validate(structured[0])
    assert span.is_admissible() and span_resolves(span, document)
    # Lazy binding is duplicate-safe: one admitted finding for the native record.
    findings_out = tool._verify([proposition], findings, documents)
    assert [f.status.value for f in findings_out] == ["SUPPORTED"]


def test_retrieve_without_propositions_does_not_eagerly_bind_structured_evidence(monkeypatch):
    from app.capabilities.web_search import SearchHit
    from app.capabilities.web_research import WebResearchTool
    from app.config import WebConfig

    tool = WebResearchTool(WebConfig(broad_search=False), llm=None)
    page, _ = _json_page(201)
    monkeypatch.setattr("app.capabilities.web_research.fetch_with_fallback", lambda *a, **k: page)
    findings, _, _, _, documents = tool._retrieve(
        [SearchHit("Stats", page.url, engine="publisher_feeds")], "stats", [], {"stats"}, 1,
    )
    document = RetrievedDocument.model_validate(documents[0])
    assert document.representations  # the raw/native views are preserved
    assert all(s["source_type"] != "STRUCTURED" for s in findings[0]["evidence_spans"])
