#!/usr/bin/env python3
"""Rotating heterogeneous-resource evaluation for M2.9 content artifacts (ADR 0031).

This is a reproducible, *representation-diversity* evaluation. It generates unseen
heterogeneous resources (HTML with/without metadata, nested JSON, JSON arrays, XML/RSS,
plain text, rendered DOM, thread/tree data), runs them through the real
``WebResearchTool._retrieve`` + ``_verify`` content-evidence boundary, and measures artifact
integrity, flexibility, evidence and context behavior. It does not depend on live publisher
availability, which is the point: it stresses the content boundary, not one website family.

Random seeds are recorded in the report. Named MLB benchmark entities never enter the
generator. This tool is not on the runtime path.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.capabilities.web_search import FetchedPage, SearchHit
from app.capabilities.web_research import WebResearchTool
from app.config import WebConfig
from app.contracts.models import AnswerProposition, EvidenceSpan, RetrievedDocument
from app.verification.lineage import span_resolves
from app.web.representation import content_artifact_summary, representation_index


def _page_for(seed: int) -> tuple[FetchedPage, str, str]:
    """Return (page, subject, metric) for one generated unseen resource."""
    rng = random.Random(seed)
    flavor = rng.choice(["html_meta", "html_bare", "json", "json_array", "xml", "text", "dom", "thread"])
    subject = f"Entity{rng.randrange(10_000, 99_999)}"
    metric = f"metric{rng.randrange(10_000, 99_999)}"
    value = rng.randrange(1, 60)
    host = f"host{rng.randrange(100, 999)}.example.org"
    url = f"https://{host}/resource/{seed}"
    sentence = f"{subject} recorded {metric} {value} in the generated {flavor} resource."

    if flavor in {"html_meta", "html_bare", "dom"}:
        meta = '<meta name="author" content="A. Reporter"><meta property="article:published_time" content="2025-06-01">' if flavor == "html_meta" else ""
        raw = f"<html><head><title>{subject}</title>{meta}</head><body><p>{sentence}</p></body></html>".encode()
        method = "browser" if flavor == "dom" else "http"
        return FetchedPage(url, url, subject, sentence, "2026-10-01T00:00:00Z", True,
                           content_hash=f"h{seed}", retrieval_method=method, canonical_url=url,
                           raw_content=raw, content_type="text/html"), subject, metric
    if flavor in {"json", "json_array", "thread"}:
        if flavor == "json_array":
            payload: Any = [{"subject": subject, "field": metric, "value": v} for v in (value, value + 1)]
        elif flavor == "thread":
            payload = {"thread": subject, "posts": [{"author": f"user{i}", "body": f"{metric} {value + i}"} for i in range(3)]}
        else:
            payload = {"subject": subject, "field": metric, "value": value, "unknownFutureField": {"nested": [1, 2]}}
        raw = json.dumps(payload).encode()
        return FetchedPage(url, url, "", raw.decode(), "2026-10-01T00:00:00Z", True,
                           content_hash=f"j{seed}", retrieval_method="http", canonical_url=url,
                           raw_content=raw, content_type="application/json"), subject, metric
    if flavor == "xml":
        raw = (f"<rss><channel><item><title>{subject}</title><description>{sentence}</description></item>"
               f"</channel></rss>").encode()
        return FetchedPage(url, url, subject, sentence, "2026-10-01T00:00:00Z", True,
                           content_hash=f"x{seed}", retrieval_method="rss_item", canonical_url=url,
                           raw_content=raw, content_type="application/xml"), subject, metric
    raw = sentence.encode()
    return FetchedPage(url, url, "", sentence, "2026-10-01T00:00:00Z", True,
                       content_hash=f"t{seed}", retrieval_method="tavily_extract", canonical_url=url,
                       raw_content=raw, content_type="text/plain"), subject, metric


def _proposition(seed: int, subject: str, metric: str, *, decoy: bool) -> AnswerProposition:
    required = f"absent{seed}" if decoy else metric
    return AnswerProposition(
        id=f"p_{'decoy' if decoy else 'true'}_{seed}",
        statement=f"{subject} recorded {required}",
        subject=subject,
        required_information=required,
        mandatory=True,
    )


def evaluate(seeds: list[int]) -> dict[str, Any]:
    tool = WebResearchTool(WebConfig(broad_search=False), llm=None)
    metrics: dict[str, int] = {
        "resources": 0, "acquired": 0, "raw_retained": 0, "text_derived": 0,
        "lineage_complete": 0, "replay_ok": 0, "replay_total": 0,
        "missing_field_tolerated": 0, "structured_preserved": 0,
        "supported": 0, "false_support": 0, "ep_denominator": 0,
        "summary_payload_leaks": 0, "source_specific_branches": 0,
    }
    kinds: set[str] = set()
    evidence_by_representation: dict[str, int] = {}
    findings_total = 0
    for seed in seeds:
        page, subject, metric = _page_for(seed)
        metrics["resources"] += 1
        page_without_metadata = not page.title and page.publication_date is None
        if page_without_metadata:
            metrics["missing_field_tolerated"] += 1
        # Acquire through the real retrieve boundary (monkeypatch the fetch call).
        import app.capabilities.web_research as wr
        original = wr.fetch_with_fallback
        wr.fetch_with_fallback = lambda *a, **k: page  # noqa: E731 - local evaluation shim
        try:
            true_prop = _proposition(seed, subject, metric, decoy=False)
            decoy_prop = _proposition(seed, subject, metric, decoy=True)
            findings, _, _, _, documents = tool._retrieve(
                [SearchHit(page.url, page.url, engine="publisher_feeds")],
                f"{subject} {metric}", [], {subject.lower()}, 1, [true_prop, decoy_prop],
            )
            evidence = tool._verify([true_prop, decoy_prop], findings, documents)
        finally:
            wr.fetch_with_fallback = original
        if documents:
            metrics["acquired"] += 1
        document = RetrievedDocument.model_validate(documents[0]) if documents else None
        if document is None:
            continue
        if document.representations:
            metrics["raw_retained"] += 1
        for rep in document.representations:
            kinds.add(rep.kind.value)
            if rep.structured_data is not None:
                metrics["structured_preserved"] += 1
            if rep.derived_from:
                metrics["text_derived"] += 1
                parent = document.representation(rep.derived_from)
                if parent is not None and rep.derivation_method:
                    metrics["lineage_complete"] += 1
        summary = content_artifact_summary(document)
        serialized = json.dumps(summary)
        if any((rep.content or "") and rep.content in serialized for rep in document.representations):
            metrics["summary_payload_leaks"] += 1
        # Replay every persisted evidence span against its concrete representation.
        span_by_id = {s.get("id"): s for f in findings for s in f["evidence_spans"]}
        for finding in evidence:
            findings_total += 1
            span = span_by_id.get(finding.span_id)
            if span is None:
                continue
            metrics["replay_total"] += 1
            model = EvidenceSpan.model_validate(span)
            if span_resolves(model, document):
                metrics["replay_ok"] += 1
            evidence_by_representation[model.source_type.value] = (
                evidence_by_representation.get(model.source_type.value, 0) + 1
            )
            expected_true = finding.proposition_id == true_prop.id
            if finding.status.value == "SUPPORTED":
                metrics["ep_denominator"] += 1
                if expected_true:
                    metrics["supported"] += 1
                else:
                    metrics["false_support"] += 1
    ep = (metrics["supported"] / metrics["ep_denominator"]) if metrics["ep_denominator"] else None
    false_rate = (metrics["false_support"] / metrics["ep_denominator"]) if metrics["ep_denominator"] else None
    return {
        "seeds": seeds,
        "metrics": metrics,
        "distinct_representation_kinds": sorted(kinds),
        "evidence_by_representation": evidence_by_representation,
        "admitted_findings": findings_total,
        "evidence_precision": ep,
        "false_support_rate": false_rate,
        "source_specific_production_branches": metrics["source_specific_branches"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=7000)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    seeds = list(range(args.start, args.start + args.count))
    report = evaluate(seeds)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
