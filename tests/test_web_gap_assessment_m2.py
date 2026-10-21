"""M2-6: retrieval-failure classification."""

from __future__ import annotations

from app.web.gap_assessment import RetrievalSignals, breakdown, classify_failure, classify_run


def test_none_when_mandatory_covered():
    assert classify_failure(RetrievalSignals(mandatory_unsupported=False)) == "NONE"


def test_source_coverage_failure_when_every_provider_failed():
    signals = RetrievalSignals(provider_calls=4, provider_errors=4, all_providers_failed=True, hits=0)
    assert classify_failure(signals) == "SOURCE_COVERAGE_FAILURE"


def test_query_failure_when_no_hits():
    assert classify_failure(RetrievalSignals(provider_calls=4, hits=0)) == "QUERY_FAILURE"


def test_ranking_failure_when_hits_but_none_relevant():
    assert classify_failure(RetrievalSignals(provider_calls=4, hits=10, relevant_hits=0)) == "RANKING_FAILURE"


def test_fetch_failure_when_relevant_but_unretrievable():
    assert classify_failure(RetrievalSignals(provider_calls=4, hits=10, relevant_hits=2, fetched_ok=0)) == "FETCH_FAILURE"


def test_span_failure_when_body_without_relevant_passage():
    assert classify_failure(RetrievalSignals(provider_calls=4, hits=10, relevant_hits=2, fetched_ok=2, selected_spans=0)) == "SPAN_FAILURE"


def test_verification_failure_when_passage_selected_but_not_supported():
    assert classify_failure(RetrievalSignals(provider_calls=4, hits=10, relevant_hits=2, fetched_ok=2, selected_spans=2, supported_findings=0)) == "VERIFICATION_FAILURE"


def test_supported_but_wrong_relation_is_span_failure():
    assert classify_failure(RetrievalSignals(provider_calls=4, hits=10, relevant_hits=2, fetched_ok=2, selected_spans=2, supported_findings=1)) == "SPAN_FAILURE"


def test_classify_run_handles_missing_discovery_counts():
    assert classify_run({"mandatory_total": 2, "mandatory_satisfied": 2}) == "NONE"
    assert classify_run({"mandatory_total": 2, "mandatory_satisfied": 0, "provider_calls": 0}) == "UNKNOWN"
    assert classify_run({"mandatory_total": 2, "mandatory_satisfied": 0, "provider_calls": 3, "fetched_documents": 0, "provider_errors": 3}) == "SOURCE_COVERAGE_FAILURE"


def test_breakdown_counts_first_failure_per_task():
    runs = {
        "a": [{"mandatory_total": 1, "mandatory_satisfied": 0, "provider_calls": 3, "fetched_documents": 0, "provider_errors": 3}],
        "b": [{"mandatory_total": 1, "mandatory_satisfied": 1}],
    }
    result = breakdown(runs)
    assert result["per_task"]["a"] == "SOURCE_COVERAGE_FAILURE"
    assert result["per_task"]["b"] == "NONE"
    assert result["counts"]["SOURCE_COVERAGE_FAILURE"] == 1
    assert result["counts"]["NONE"] == 1
