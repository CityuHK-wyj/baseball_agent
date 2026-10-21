"""web_research: open semantic web investigation with provenance and evidence spans.

ADR 0007/0017/0022: a web request is an open objective plus candidate subjects, aliases,
user wording and alternative search terms. It is not a closed DSL. The lane distinguishes:

- source discovery (search hit)          -> never grounds a claim;
- page retrieval                         -> a fetched body with a content hash;
- evidence extraction / claim grounding  -> proposition-specific span verification;
- source failure                          -> transport error, typed and retryable;
- irrelevant result                      -> fetched but does not mention the subject;
- insufficient result                    -> subject-relevant but no span establishes the
                                            requested proposition;
- contradictory result                   -> a span conflicts with the proposition.

A candidate (not-yet-canonical) entity name is a valid search subject. ``SUCCESS`` never
means "a subject token matched"; it means at least one requested proposition is supported
by an inspectable span.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from datetime import date
from typing import Any
from urllib.parse import urlparse

from app.capabilities.base import CapabilityResult, CapabilitySpec, FAMILY_WEB_EXTERNAL_RESEARCH
from app.capabilities.web_search import DISCOVERY_ONLY_ENGINES, SearchHit, WebSearchConfig, build_backends, build_broad_backends, evidence_spans, fetch_page
from app.contracts.models import (
    AnswerProposition,
    Artifact,
    CoverageState,
    DateRange,
    EvidenceFinding,
    EvidenceSpan,
    Export,
    PropositionKind,
    Reference,
    ReferenceType,
    RepresentationKind,
    RetrievedDocument,
    Scope,
    ToolOutcome,
    ToolRequest,
    VerificationStatus,
)
from app.verification.evidence import verify_span
from app.verification.propositions import required_tokens as _required_tokens
from app.web.budget import GoalResearchBudget
from app.web.language import build_policy, detect_language
from app.web.outcomes import SourceOutcome, classify_search_result
from app.web.query_plan import (
    ResearchQueryPlan,
    build_query_plan,
    merge_llm_variants,
    plan_texts,
)
from app.web.ranking import CandidateContext, rank_candidates
from app.web.sources.base import descriptor_to_dict
from app.web.sources.encyclopedia import WikipediaSource
from app.web.span_selector import relationship_tokens as _relationship_tokens
from app.web.span_selector import select_span_dicts, subject_signature_present, support_eligible
from app.semantic.relation_attribution import directed_support
from app.web.source_intent import SourceIntent, infer_source_intent, source_type_for_hit
from app.web.fetch_ladder import fetch_with_fallback
from app.web.representation import (
    build_representations,
    content_artifact_summary,
    representation_index,
    structured_evidence_spans,
)
from app.web.sources.tavily import TavilySearchBackend, extract_representation
from app.web.versioning import EXTRACTOR_VERSION, body_hash
from app.web.agentic_resolution import AgenticContentResolver, ResolutionReason

# A page must mention one of these to count as evidence about the subject.
_MIN_SUBJECT_OVERLAP = 1
# Cap the number of LLM verification calls per web request (cost/latency bound).
_LLM_VERIFY_BUDGET = 6


class _CountingLLM:
    """Count provider attempts before the call, including failed or rejected attempts."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    @property
    def configured(self) -> bool:
        return bool(getattr(self._inner, "configured", False))

    def complete_json(self, *args, **kwargs):
        # Count before delegating so a raising provider still spends budget.
        self.calls += 1
        return self._inner.complete_json(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)
# Cap how many query variants the broad-discovery escalation may run.
_BROAD_SEARCH_VARIANTS = 3
# Cap how many extra bodies the post-verification escalation may fetch.
_BROAD_FETCH_BUDGET = 3


class WebResearchTool:
    def __init__(self, config, llm=None):
        self.config = config
        self.llm = llm
        self.search_config = WebSearchConfig(
            search_endpoint=getattr(config, "search_endpoint", ""),
            search_api_key=getattr(config, "search_api_key", ""),
            user_agent=getattr(config, "user_agent", "BaseballAgent/0.1"),
            timeout=getattr(config, "fetch_timeout", 15.0),
        )
        self.backends = build_backends(self.search_config, getattr(config, "feeds", None))
        self.tavily_key = getattr(config, "tavily_api_key", "") or ""
        self.tavily_backend = TavilySearchBackend(self.tavily_key, self.search_config) if self.tavily_key else None
        # Broad discovery is a real escalation path, not a default source.
        self.broad_backends = (
            build_broad_backends(self.search_config) if getattr(config, "broad_search", True) else []
        )
        if self.tavily_backend is not None and getattr(config, "broad_search", True):
            self.broad_backends.append(self.tavily_backend)
        # Minimal source contract (M1-T4): descriptors are metadata; selection is unchanged.
        self.wikipedia = WikipediaSource(self.search_config)
        self.sources = [self.wikipedia]
        if self.tavily_backend is not None:
            self.sources.append(self.tavily_backend)
        # M1 observability state (reset at the start of each execute()).
        self._source_calls: dict[str, int] = {}
        self._source_outcomes: list[dict[str, Any]] = []
        self._fetch_attempts: list[dict[str, Any]] = []
        self._source_intent = SourceIntent.ANALYSIS
        self._broad_search_used = False
        self._broad_search_calls = 0
        self._usage_start = 0
        self._content_resolver = AgenticContentResolver(self.llm)
        self._resolution_goal_id = ""
        self._resolution_seen_uris: set[str] = set()
        self._resolution_depths: dict[str, int] = {}
        self.spec = CapabilitySpec(
            name="web_research",
            description="Open web research returning sourced evidence spans; accepts candidate names.",
            accepts=["SEARCH_QUERY"],
            produces=["WEB_EVIDENCE"],
            required_inputs=[],
            family=FAMILY_WEB_EXTERNAL_RESEARCH,
        )

    def execute(self, request: ToolRequest, ctx=None) -> CapabilityResult:
        objective = request.objective or request.inputs.get("objective", "")
        user_wording = str(request.inputs.get("user_wording") or request.inputs.get("raw_query") or "")
        language = request.inputs.get("language", detect_language(objective))
        self._llm_verify_calls = 0
        self._llm_query_calls = 0
        # M1 observability: per-source call counts/outcomes and research LLM usage.
        self._source_calls: dict[str, int] = {}
        self._source_outcomes: list[dict[str, Any]] = []
        self._fetch_attempts = []
        goal_id = str(getattr(getattr(ctx, "goal", None), "id", "") or request.id)
        if goal_id != self._resolution_goal_id:
            self._content_resolver = AgenticContentResolver(self.llm)
            self._resolution_seen_uris = set()
            self._resolution_depths = {}
            self._resolution_goal_id = goal_id
        self._usage_start = len(getattr(self.llm, "usage_records", []) or []) if self.llm else 0
        self._source_intent = infer_source_intent(f"{objective} {user_wording}")
        subjects = self._subjects(request)
        subject_tokens = _subject_tokens(subjects)
        propositions = _parse_propositions(request.inputs.get("propositions"))
        max_fetches = int(request.max_fetches or request.inputs.get("max_fetches") or self.config.max_fetches)

        policy = build_policy(
            user_language=language,
            goal_text=f"{objective} {user_wording}",
            source_language=detect_language(objective),
        )
        plan = self._build_query_plan(request, objective, language, subjects, user_wording)
        queries = plan_texts(plan)
        latency = getattr(ctx, "latency", None)
        # M1 has no retrieval cache; record the disabled state explicitly so the stage is
        # observable and later M3 cache behavior can be compared against a real zero.
        _record_latency(latency, "web:cache", time.monotonic(), "disabled:m1")
        started = time.monotonic()
        hits, backend_errors, attempted = self._search(plan, language, latency=latency, backends=self.backends)
        _record_latency(latency, "web:search", started, request.need_id)
        # M2.5 source-coverage escalation: only when the direct sources produced no hit that
        # mentions the subject do we consult a broad-web discovery surface. Broad hits are
        # candidates, never trusted evidence, and are fetched and verified like any other.
        if self._needs_broad_search(hits, subject_tokens, _required_hint_tokens(propositions)):
            started = time.monotonic()
            broad_hits, broad_errors, broad_attempted = self._search(
                plan, language, latency=latency, backends=self.broad_backends, max_variants=_BROAD_SEARCH_VARIANTS
            )
            _record_latency(latency, "web:broad_search", started, request.need_id)
            self._broad_search_used = True
            self._broad_search_calls += broad_attempted
            broad_hits = _filter_broad_hits(broad_hits, subject_tokens)
            hits = _merge_hits(hits, broad_hits)
            backend_errors.extend(broad_errors)
        # M2-3: rank candidates by the requested relationship, not by subject-only overlap or
        # raw provider order. Ranking is deterministic (stable URL/title tie-break).
        context = self._candidate_context(plan, subjects, propositions)
        hits = rank_candidates(hits, context)
        # SourcePlan-lite (M1-T5): a deterministic historical-explanation plan promotes
        # Wikipedia above the ranked order. It reads only proposition kind/scope, not
        # any player or event name.
        hits = _apply_source_plan(hits, propositions, objective, self._source_intent)

        if not hits:
            outcomes = [SourceOutcome(row["status"]) for row in self._source_outcomes]
            # SEARCH_BACKEND_UNAVAILABLE only when every attempted provider failed; a genuine
            # empty response (even from one provider) stays NO_RESULTS (L6 fix).
            if outcomes and all(outcome != SourceOutcome.EMPTY for outcome in outcomes):
                status, code, classification = "FAILED", "SEARCH_BACKEND_UNAVAILABLE", "RETRYABLE_SOURCE"
                message = "all search backends failed"
            else:
                status, code, classification = "NO_DATA", "NO_RESULTS", "RETRYABLE_SOURCE"
                message = "search returned no results across the attempted backends"
            # The typed per-source outcomes are persisted even when no provider returned a
            # hit, so a dead provider is auditable and never conflated with an empty result.
            artifact = self._artifact(
                request, objective, queries, [], [], 0, [], subjects,
                documents=[], evidence_findings=[], coverage={},
                retrieval_code=code, propositions=propositions, plan=plan,
            )
            self._record_research_diagnostics(ctx, artifact)
            return CapabilityResult(
                artifact,
                ToolOutcome(status=status, code=code, message=message, retryable=True, need_id=request.need_id, tool_request_id=request.id, classification=classification),
            )

        started = time.monotonic()
        findings, discovery, fetched, errors, documents = self._retrieve(hits, objective, queries, subject_tokens, max_fetches, propositions, latency=latency)
        _record_latency(latency, "web:retrieve", started, request.need_id)

        # Dynamic query update: the first query set produced hits but no subject-relevant
        # page. Try one alternate query built from the candidate subject + user wording.
        if not any(f.get("relevant") for f in findings) and user_wording:
            alternate = " ".join([*subjects[:2], user_wording])[:200]
            if alternate and alternate not in queries:
                alt_plan = ResearchQueryPlan(
                    objective=objective,
                    subjects=tuple(subjects),
                    aliases=plan.aliases,
                    variants=(_alternate_variant(alternate),),
                    freshness=plan.freshness,
                    generated_by="alternate",
                    research_languages=plan.research_languages,
                    expansion_reasons=plan.expansion_reasons,
                )
                alt_hits, extra_errors, _ = self._search(alt_plan, language, latency=latency)
                alt_hits = rank_candidates(alt_hits, self._candidate_context(alt_plan, subjects, propositions))
                backend_errors.extend(extra_errors)
                new_findings, extra_discovery, extra_fetched, extra_errors2, extra_documents = self._retrieve(
                    alt_hits, objective, [alternate], subject_tokens, max(0, max_fetches - fetched), propositions, latency=latency
                )
                findings.extend(new_findings)
                discovery.extend(extra_discovery)
                documents.extend(extra_documents)
                fetched += extra_fetched
                errors.extend(extra_errors2)
                queries.append(alternate)

        if not findings:
            # Hits existed but no page could be retrieved: source fragility, not absence.
            # The failed documents are still recorded so the retrieval failure is inspectable.
            artifact = self._artifact(
                request, objective, queries, findings, discovery, fetched, errors, subjects,
                documents=documents, evidence_findings=[], coverage={},
                retrieval_code="SOURCE_UNAVAILABLE", propositions=propositions, plan=plan,
            )
            self._record_research_diagnostics(ctx, artifact)
            return CapabilityResult(
                artifact,
                ToolOutcome(status="FAILED", code="SOURCE_UNAVAILABLE", message=f"search hits existed but no page could be retrieved ({'; '.join(errors[:3])})", retryable=True, need_id=request.need_id, tool_request_id=request.id, classification="RETRYABLE_SOURCE"),
            )

        started = time.monotonic()
        evidence_findings = self._verify(propositions, findings, documents)
        _record_latency(latency, "web:verify", started, request.need_id)
        # M2.5 post-verification source-coverage escalation: if no mandatory prose proposition is
        # supported after the direct pass, the required information was not discovered. Consult
        # the real broad-discovery surface once and verify the additional fetched bodies.
        if (
            propositions
            and not self._broad_search_used
            and self.broad_backends
            and not _has_mandatory_support(propositions, evidence_findings)
        ):
            started = time.monotonic()
            broad_hits, broad_errors, broad_attempted = self._search(
                plan, language, latency=latency, backends=self.broad_backends, max_variants=_BROAD_SEARCH_VARIANTS
            )
            _record_latency(latency, "web:broad_search", started, request.need_id)
            self._broad_search_used = True
            self._broad_search_calls += broad_attempted
            backend_errors.extend(broad_errors)
            existing_urls = {str(f.get("url") or "") for f in findings}
            broad_hits = [hit for hit in _filter_broad_hits(broad_hits, subject_tokens) if getattr(hit, "url", "") not in existing_urls]
            if broad_hits:
                new_findings, extra_discovery, extra_fetched, extra_errors, extra_documents = self._retrieve(
                    broad_hits, objective, queries, subject_tokens, _BROAD_FETCH_BUDGET, propositions, latency=latency
                )
                findings.extend(new_findings)
                discovery.extend(extra_discovery)
                documents.extend(extra_documents)
                fetched += extra_fetched
                errors.extend(extra_errors)
                evidence_findings = self._verify(propositions, findings, documents)
        if propositions:
            self._attach_proposition_findings(findings, evidence_findings)
        coverage = _coverage(propositions, evidence_findings)
        status, code, message, artifact_status = self._classify(propositions, findings, evidence_findings)

        artifact = self._artifact(
            request, objective, queries, findings, discovery, fetched, errors, subjects,
            documents=documents, evidence_findings=evidence_findings, coverage=coverage,
            retrieval_code=code, propositions=propositions, plan=plan,
        )
        artifact.status = artifact_status
        self._record_research_diagnostics(ctx, artifact)
        return CapabilityResult(
            artifact,
            ToolOutcome(status=status, code=code, message=message, retryable=status != "SUCCESS", need_id=request.need_id, tool_request_id=request.id),
        )

    # ------------------------------------------------------------------ helpers
    def _subjects(self, request: ToolRequest) -> list[str]:
        subjects: list[str] = []
        for key in ("entity_names", "candidate_names"):
            for item in request.inputs.get(key) or []:
                text = str(item).strip()
                if text and text not in subjects:
                    subjects.append(text)
        for mention in request.inputs.get("mentions") or []:
            text = str(mention.get("mention") if isinstance(mention, dict) else mention).strip()
            if text and text not in subjects:
                subjects.append(text)
        return subjects

    def _search(
        self,
        plan: ResearchQueryPlan,
        language: str,
        latency: Any = None,
        *,
        backends: list | None = None,
        max_variants: int | None = None,
    ) -> tuple[list, list[str], int]:
        seen: set[str] = set()
        backend_errors: list[str] = []
        attempted = 0
        hits: list = []
        active_backends = self.backends if backends is None else backends
        variants = list(plan.variants)
        if max_variants is not None:
            variants = variants[:max_variants]
        for variant in variants:
            for backend in active_backends:
                attempted += 1
                started = time.monotonic()
                raised: BaseException | None = None
                try:
                    hits_for_backend = backend.search(variant.text, limit=4, language=variant.language or language)
                except Exception as exc:  # noqa: BLE001 - a provider must never crash research
                    hits_for_backend = []
                    raised = exc
                _record_latency(latency, f"web:source:{backend.name}", started, variant.text)
                self._source_calls[backend.name] = self._source_calls.get(backend.name, 0) + 1
                last_error = getattr(backend, "last_error", "") or (
                    f"{type(raised).__name__}: {raised}" if raised else ""
                )
                outcome = classify_search_result(hits=hits_for_backend, error=last_error, raised=raised)
                self._source_outcomes.append(
                    {"source": backend.name, "status": outcome.value, "hits": len(hits_for_backend),
                     "credits": getattr(backend, "last_usage_credits", None) if backend.name == "tavily" else None}
                )
                if outcome not in (SourceOutcome.OK, SourceOutcome.EMPTY):
                    backend_errors.append(f"{backend.name}:{last_error}")
                for hit in hits_for_backend:
                    if hit.url in seen:
                        continue
                    seen.add(hit.url)
                    # Tag the hit with the facet that discovered it so the relationship-aware
                    # ranker can use the facet's required information/scope (M2-2/M2-3).
                    _tag_hit(hit, variant, backend.name)
                    hits.append(hit)
            if len(hits) >= 12:
                break
        # ``web:rank`` now measures the deterministic relationship-aware ranking step. The
        # actual ranking happens in execute() where the proposition context is available; this
        # span records the discovery merge so the stage stays observable.
        started = time.monotonic()
        _record_latency(latency, "web:rank", started, "relationship_aware")
        return hits, backend_errors, attempted

    def _needs_broad_search(self, hits: list, subject_tokens: set[str], required_tokens: set[str] | None = None) -> bool:
        """Broad discovery is justified only when direct sources are thin or relation-blind.

        A hit that names the subject is not enough: a direct candidate must also carry some of
        the requested relation's information. This prevents a subject page (e.g. a player
        biography) from masking a genuine source-coverage gap for an interview/community
        obligation.
        """
        if not self.broad_backends:
            return False
        if getattr(self, "_source_intent", SourceIntent.ANALYSIS) in {
            SourceIntent.COMMUNITY_DISCUSSION, SourceIntent.LOCAL_REPORTING
        }:
            # The configured direct feeds and encyclopedia do not represent the
            # requested community or local press environment by themselves.
            return True
        required_tokens = required_tokens or set()
        if not hits:
            return True
        for hit in hits[:10]:
            if (getattr(self, "_source_intent", SourceIntent.ANALYSIS) in {
                    SourceIntent.INTERVIEW, SourceIntent.PRIMARY_STATEMENT
                } and str(getattr(hit, "engine", "") or "") == "wikipedia"):
                continue
            if str(getattr(hit, "engine", "") or "") in DISCOVERY_ONLY_ENGINES:
                # A discovery-only hit (e.g. a Google News redirect) cannot be fetched into a
                # body, so it does not close the source-coverage gap.
                continue
            haystack = f"{getattr(hit, 'title', '') or ''} {getattr(hit, 'snippet', '') or ''}".lower()
            if subject_tokens and not any(token in haystack for token in subject_tokens):
                continue
            if required_tokens and not any(token in haystack for token in required_tokens):
                continue
            return False
        return True

    def _retrieve(self, hits, objective: str, queries: list[str], subject_tokens: set[str], budget: int, propositions: list[AnswerProposition] | None = None, latency=None):
        findings: list[dict[str, Any]] = []
        discovery: list[dict[str, Any]] = []
        documents: list[dict[str, Any]] = []
        fetched = 0
        errors: list[str] = []
        seen_sources: set[str] = set()
        seen_bodies: set[str] = set()
        pending_hits = list(hits)
        for hit in pending_hits:
            if hit.engine in DISCOVERY_ONLY_ENGINES:
                discovery.append({"title": hit.title, "url": hit.url, "snippet": hit.snippet, "engine": hit.engine})
            if fetched >= budget:
                break
            started = time.monotonic()
            page = fetch_with_fallback(hit.url, self.search_config,
                                       browser_enabled=bool(getattr(self.config, "browser_render", False)),
                                       fetch=fetch_page,
                                       api_extract=(lambda url, config: extract_representation(url, self.tavily_key, config)) if self.tavily_key else None)
            if (hit.engine in DISCOVERY_ONLY_ENGINES and hit.url not in self._resolution_depths and page.ok
                    and urlparse(page.final_url).hostname == urlparse(hit.url).hostname):
                # An aggregator landing page is still discovery. Only a redirect to
                # the publisher (or another explicit full-text representation) can
                # become a source body.
                page.ok = False
                page.error_code = "DISCOVERY_ONLY"
                page.text = ""
            _record_latency(latency, "web:fetch", started, hit.url)
            fetched += 1
            extractor_version = {
                "rss_item": "rss-item-v1", "browser": "browser-dom-v1",
                "tavily_extract": "tavily-extract-v1",
            }.get(page.retrieval_method, EXTRACTOR_VERSION)
            document = RetrievedDocument(
                url=hit.url,
                final_url=page.final_url or hit.url,
                provider=hit.engine,
                title=page.title or hit.title,
                publication_date=page.publication_date,
                retrieved_at=page.retrieved_at,
                retrieval_status="OK" if page.ok else (page.error_code or "FETCH_FAILED"),
                error_code="" if page.ok else page.error_code,
                content_hash=page.content_hash,
                text_excerpt=(page.text or "")[:4000],
                body_text=page.text or "",
                source_quality="unassessed",
                initial_url=hit.url,
                canonical_url=page.canonical_url or page.final_url,
                retrieval_method=page.retrieval_method,
                fallback_steps=list(page.fallback_steps),
                source_type=source_type_for_hit(hit.engine, self._source_intent),
                source_language=detect_language(page.text),
                representation_provider="tavily" if page.retrieval_method == "tavily_extract" else "",
                response_status=page.status_code,
                elapsed_ms=page.elapsed_ms,
            )
            # ADR 0031: preserve the acquired representations. The extracted text view stays
            # primary so the existing offset evidence path is unchanged; native structured
            # payloads remain available for structured evidence and lazy derivation.
            document.representations = build_representations(page, extractor_version=extractor_version)
            text_reps = [item for item in document.representations if item.kind == RepresentationKind.TEXT]
            document.primary_representation_id = (
                text_reps[0].id if text_reps
                else (document.representations[0].id if document.representations else "")
            )
            document.source_uri = document.canonical_url or document.final_url or document.url
            # Escalate only for a concrete failure on a potentially relevant acquired
            # resource. Source text is never used as an instruction or evidence decision.
            native = next((item for item in document.representations if item.kind != RepresentationKind.TEXT and item.content), None)
            raw_lower = native.content.lower() if native else ""
            target_terms = _required_hint_tokens(propositions or [])
            related = bool(subject_tokens and any(token in f"{hit.title} {hit.snippet} {raw_lower[:50000]}".lower() for token in subject_tokens))
            reason = None
            if native and related and (page.error_code in {"NO_BODY", "DISCOVERY_ONLY"} or not page.text.strip()):
                reason = ResolutionReason.INTERMEDIATE_RESOURCE if hit.engine in DISCOVERY_ONLY_ENGINES else ResolutionReason.NO_USABLE_BODY
            elif native and related and page.ok and target_terms and not any(token in page.text.lower() for token in target_terms) and any(token in raw_lower for token in target_terms):
                reason = ResolutionReason.TEXT_DERIVATION_INSUFFICIENT
            if reason is not None:
                resolved = self._content_resolver.resolve(
                    document, objective=objective, reason=reason,
                    depth=self._resolution_depths.get(hit.url, 0),
                )
                document.resolution_traces.append(resolved.trace)
                if resolved.representation is not None:
                    document.representations.append(resolved.representation)
                    if resolved.representation.kind == RepresentationKind.TEXT:
                        page.text = resolved.representation.content
                        page.ok = True
                        page.error_code = ""
                        document.body_text = page.text
                        document.text_excerpt = page.text[:4000]
                        document.primary_representation_id = resolved.representation.id
                        document.retrieval_status = "OK"
                        document.error_code = ""
                    elif resolved.representation.kind == RepresentationKind.STRUCTURED:
                        page.ok = True
                        page.error_code = ""
                        document.retrieval_status = "OK"
                        document.error_code = ""
                elif resolved.uri and resolved.uri not in self._resolution_seen_uris and resolved.uri != hit.url and fetched < budget:
                    self._resolution_seen_uris.add(resolved.uri)
                    self._resolution_depths[resolved.uri] = self._resolution_depths.get(hit.url, 0) + 1
                    pending_hits.append(SearchHit(title=hit.title, url=resolved.uri, snippet="", engine=hit.engine))
            # M1-T6: additive body-version metadata for replay/cache without stale offsets.
            document_payload = document.model_dump()
            document_payload["raw_content_hash"] = document.content_hash
            document_payload["body_hash"] = body_hash(document.body_text)
            document_payload["extractor_version"] = extractor_version
            document_payload["initial_url"] = hit.url
            document_payload["canonical_url"] = page.canonical_url or page.final_url
            document_payload["retrieval_method"] = page.retrieval_method
            document_payload["fallback_steps"] = list(page.fallback_steps)
            document_payload["source_type"] = source_type_for_hit(hit.engine, self._source_intent)
            document_payload["response_status"] = page.status_code
            document_payload["elapsed_ms"] = page.elapsed_ms
            document_payload["source_language"] = detect_language(page.text)
            document_payload["representation_provider"] = "tavily" if page.retrieval_method == "tavily_extract" else ""
            self._fetch_attempts.append({
                "initial_url": hit.url, "final_url": page.final_url,
                "canonical_url": page.canonical_url or page.final_url,
                "source_type": source_type_for_hit(hit.engine, self._source_intent),
                "fetch_method": page.retrieval_method, "fallback_steps": list(page.fallback_steps),
                "outcome": "SUCCESS" if page.ok else (page.error_code or "FETCH_ERROR"),
                "response_status": page.status_code, "body_obtained": bool(page.ok and page.text),
                "extraction_outcome": "SUCCESS" if page.ok else (page.error_code or "NO_BODY"),
                "elapsed_ms": page.elapsed_ms,
                "credential_dependency": "TAVILY_API_KEY" if page.retrieval_method == "tavily_extract" else "",
                "provider_credits": page.provider_credits,
            })
            source_key = (page.canonical_url or page.final_url).rstrip("/").lower()
            body_key = body_hash(page.text or "") if page.ok else ""
            if page.ok and (source_key in seen_sources or body_key in seen_bodies):
                self._fetch_attempts[-1]["outcome"] = "DUPLICATE_REPRESENTATION"
                continue
            if page.ok:
                seen_sources.add(source_key)
                seen_bodies.add(body_key)
            documents.append(document_payload)
            if not page.ok:
                errors.append(f"{page.error_code}:{hit.url}")
                continue
            spans = select_span_dicts(
                page.text,
                query=f"{objective} {queries[0] if queries else ''} {' '.join(subject_tokens)}",
                propositions=propositions or [],
                max_spans=6,
                document_title=page.title or hit.title,
            )
            persisted_spans: list[dict[str, Any]] = []
            for span_data in spans:
                span = EvidenceSpan(
                    document_id=document.id,
                    representation_id=span_data.get("representation_id") or document.primary_representation_id,
                    text=span_data.get("text", ""),
                    start=span_data.get("start"),
                    end=span_data.get("end"),
                    location=span_data.get("location", ""),
                    source_type=span_data.get("source_type", "DOCUMENT_BODY"),
                    retrieval_status=span_data.get("retrieval_status", "OK"),
                    locator_type=span_data.get("locator_type", "char_offsets"),
                    locator=span_data.get("locator", ""),
                )
                # Offsets must reproduce the stored text; a span that cannot is dropped rather
                # than persisted as an uninspectable pseudo-passage.
                if span.text and page.text[span.start : span.end] != span.text:
                    continue
                persisted = span.model_dump(mode="json")
                # M2 candidate-admission metadata: ignored by EvidenceSpan and never treated
                # as an evidence field, but lets the verifier skip subject-only fragments.
                persisted["quality"] = span_data.get("quality", "prose")
                persisted["required_matched"] = list(span_data.get("required_matched", []))
                persisted["span_score"] = span_data.get("score")
                persisted_spans.append(persisted)
            # Lazy structured binding: only when a proposition is actually being evaluated and
            # the document retained a native structured representation. Structured records keep
            # their JSON Pointer rather than being degraded into prose offsets.
            if propositions:
                existing_structured = {(item.get("representation_id"), item.get("locator")) for item in persisted_spans}
                for span_data in structured_evidence_spans(document):
                    key = (span_data.get("representation_id"), span_data.get("locator"))
                    if key in existing_structured:
                        continue
                    structured_span = EvidenceSpan(
                        document_id=document.id,
                        representation_id=span_data["representation_id"],
                        text=span_data["text"],
                        location=span_data["location"],
                        source_type=span_data["source_type"],
                        retrieval_status=span_data["retrieval_status"],
                        locator_type=span_data["locator_type"],
                        locator=span_data["locator"],
                    )
                    persisted = structured_span.model_dump(mode="json")
                    persisted["quality"] = span_data.get("quality", "structured")
                    persisted["required_matched"] = list(span_data.get("required_matched", []))
                    persisted["span_score"] = span_data.get("score")
                    persisted_spans.append(persisted)
            relevant = _mentions_subject(page.title or hit.title, " ".join(s.get("text", "") for s in persisted_spans), subject_tokens)
            findings.append(
                {
                    "document_id": document.id,
                    "url": page.final_url,
                    "final_url": page.final_url,
                    "title": page.title or hit.title,
                    "snippet": hit.snippet,
                    "retrieved_at": page.retrieved_at,
                    "publication_date": page.publication_date,
                    "content_hash": page.content_hash,
                    "raw_content_hash": page.content_hash,
                    "body_hash": body_hash(page.text or ""),
                    "extractor_version": extractor_version,
                    "engine": hit.engine,
                    "source_type": source_type_for_hit(hit.engine, self._source_intent),
                    "retrieval_method": page.retrieval_method,
                    "canonical_url": page.canonical_url or page.final_url,
                    "source_language": detect_language(page.text),
                    "representation_provider": "tavily" if page.retrieval_method == "tavily_extract" else "",
                    "evidence_spans": persisted_spans,
                    "grounded": bool(persisted_spans),
                    "relevant": relevant,
                }
            )
        return findings, discovery, fetched, errors, documents

    def _verify(
        self, propositions: list[AnswerProposition], findings: list[dict[str, Any]], documents: list[dict[str, Any]]
    ) -> list[EvidenceFinding]:
        if not propositions:
            return []
        documents_by_id = {doc.get("id"): doc for doc in documents}
        candidates: list[tuple[EvidenceFinding, AnswerProposition, EvidenceSpan, RetrievedDocument | None, float]] = []
        # A native structured record and its derived text view describe the same content. Keep
        # one candidate per (proposition, canonical content) so the same fact is not admitted
        # twice merely because the artifact offers two representations of it (ADR 0031).
        seen_candidate_content: set[tuple[str, str]] = set()
        for finding in findings:
            document_payload = documents_by_id.get(finding.get("document_id"))
            document = RetrievedDocument.model_validate(document_payload) if document_payload else None
            for proposition in propositions:
                # The candidate-admission gate cares about the *relationship* tokens; subject
                # tokens alone must never admit a subject-only citation/metadata fragment.
                required = _relationship_tokens(proposition)
                for span_data in finding.get("evidence_spans", []):
                    # M2-4 pre-admission: an obvious citation/navigation/metadata fragment that
                    # does not carry a requested relationship token never becomes a candidate.
                    # This filters inputs to the unchanged verifier; it cannot upgrade support.
                    if not support_eligible(span_data, required):
                        continue
                    if not directed_support(proposition, str(span_data.get("text") or "")):
                        continue
                    # Entity disambiguation at candidate admission: a passage must carry the
                    # subject's distinguishing token, so a shared given name cannot turn a
                    # different person into support. This precedes the unchanged verifier.
                    if not subject_signature_present(str(span_data.get("text") or ""), proposition.subject):
                        continue
                    try:
                        span = EvidenceSpan.model_validate(span_data)
                    except Exception:  # noqa: BLE001 - a malformed span must not become evidence
                        continue
                    deterministic = verify_span(proposition, span, document, llm=None, allow_llm=False)
                    if deterministic.status == VerificationStatus.IRRELEVANT:
                        continue
                    content_key = (proposition.id, _evidence_content_key(str(span_data.get("text") or "")))
                    if content_key in seen_candidate_content:
                        continue
                    seen_candidate_content.add(content_key)
                    score = float(span_data.get("span_score") or 0.0)
                    candidates.append((deterministic, proposition, span, document, score))
        # Spend the bounded LLM budget on the spans that matter: likely supports/contradictions
        # first, then the most relationship-relevant insufficiencies (highest passage score).
        # This prevents an early irrelevant page from consuming the budget and forcing a weak
        # deterministic decision later.
        priority = {
            VerificationStatus.SUPPORTED: 0,
            VerificationStatus.CONTRADICTED: 0,
            VerificationStatus.INSUFFICIENT: 1,
        }
        candidates.sort(key=lambda item: (priority.get(item[0].status, 2), -item[4]))
        results: list[EvidenceFinding] = []
        # Count every provider attempt, including one that raises or is later rejected. A
        # failed or re-viewed call still spends budget and must be reported truthfully.
        counter = _CountingLLM(self.llm)
        for deterministic, proposition, span, document, _score in candidates:
            finding_result = deterministic
            if counter.calls < _LLM_VERIFY_BUDGET:
                refined = verify_span(proposition, span, document, llm=counter, allow_llm=True)
                if refined.verifier == "llm":
                    finding_result = refined
            span.verification = finding_result.status
            span.establishes = finding_result.what_it_establishes
            span.proposition_ids = [proposition.id]
            results.append(finding_result)
        self._llm_verify_calls = counter.calls
        return results

    def _attach_proposition_findings(self, findings: list[dict[str, Any]], evidence_findings: list[EvidenceFinding]) -> None:
        by_document: dict[str, list[EvidenceFinding]] = {}
        for item in evidence_findings:
            by_document.setdefault(item.document_id, []).append(item)
        for finding in findings:
            items = by_document.get(finding.get("document_id", ""), [])
            finding["proposition_findings"] = [item.model_dump() for item in items]
            finding["proposition_ids"] = sorted({item.proposition_id for item in items})

    def _classify(
        self,
        propositions: list[AnswerProposition],
        findings: list[dict[str, Any]],
        evidence_findings: list[EvidenceFinding],
    ) -> tuple[str, str, str, str]:
        if not propositions:
            if not any(f.get("relevant") for f in findings):
                return ("NO_DATA", "IRRELEVANT_RESULTS", "retrieved pages did not mention the subject", "PARTIAL")
            return ("SUCCESS", "OK", f"{sum(1 for f in findings if f.get('relevant'))} relevant grounded source(s)", "ACCEPTED")
        mandatory = [p for p in propositions if p.mandatory] or propositions
        statuses_by_prop: dict[str, set[str]] = {}
        for item in evidence_findings:
            statuses_by_prop.setdefault(item.proposition_id, set()).add(item.status.value)
        supported = [
            p for p in mandatory
            if VerificationStatus.SUPPORTED.value in statuses_by_prop.get(p.id, set())
        ]
        contradicted = [
            p for p in mandatory
            if VerificationStatus.CONTRADICTED.value in statuses_by_prop.get(p.id, set())
        ]
        if supported and contradicted:
            return ("SUCCESS", "CONFLICTING_EVIDENCE", "grounded sources disagree; conflict retained", "PARTIAL")
        if supported and len(supported) == len(mandatory):
            return ("SUCCESS", "OK", f"{len(supported)} proposition(s) supported by grounded evidence", "ACCEPTED")
        if supported:
            return ("SUCCESS", "PARTIAL_EVIDENCE", f"{len(supported)}/{len(mandatory)} proposition(s) supported", "PARTIAL")
        if contradicted:
            return ("NO_DATA", "CONTRADICTED_EVIDENCE", "retrieved evidence contradicts the requested proposition", "PARTIAL")
        if any(f.get("relevant") for f in findings):
            return ("NO_DATA", "INSUFFICIENT_EVIDENCE", "retrieved pages mention the subject but do not establish the requested proposition", "PARTIAL")
        return ("NO_DATA", "IRRELEVANT_RESULTS", "retrieved pages did not mention the subject", "PARTIAL")

    def _artifact(
        self, request, objective, queries, findings, discovery, fetched, errors, subjects, *,
        documents, evidence_findings, coverage, retrieval_code, propositions, plan=None,
    ) -> Artifact:
        historical = bool(re.search(r"(19|20)\d{2}", " ".join(queries))) or any(
            marker in objective.lower() for marker in ("history", "historical", "历史", "背景", "origin")
        )
        season = next((p.time_scope.get("season") for p in propositions if p.time_scope.get("season")), None)
        population = next((p.population for p in propositions if p.population), None)
        return Artifact(
            kind="WEB_EVIDENCE",
            structured_data={
                "objective": objective,
                "subjects": subjects,
                "queries": queries,
                "provider": sorted({f.get("engine", "") for f in findings}),
                "findings": findings,
                "documents": documents,
                # ADR 0031 progressive disclosure: a payload-free index of available views.
                # The full representations stay on each document and are loaded only when a
                # downstream step asks for one; they are never pushed into model context here.
                "content_artifacts": [content_artifact_summary(doc) for doc in documents],
                "evidence_spans": _flatten_spans(findings),
                "proposition_verification": bool(propositions),
                "evidence_findings": [item.model_dump() for item in evidence_findings],
                "proposition_coverage": coverage,
                "retrieval_status": retrieval_code,
                "discovery_hits": discovery,
                "errors": errors,
                "source_outcomes": [dict(item) for item in self._source_outcomes],
                "fetch_attempts": list(getattr(self, "_fetch_attempts", [])),
                "source_intent": getattr(self, "_source_intent", SourceIntent.ANALYSIS).value,
                "credential_dependencies": [] if self.tavily_key else ["TAVILY_API_KEY"],
                "historical_capable": historical,
                "source_quality": "mixed_sources_unassessed",
                "source_plan": _source_plan(propositions, objective, getattr(self, "_source_intent", SourceIntent.ANALYSIS)),
                "sources": [descriptor_to_dict(source.descriptor) for source in self.sources],
                "query_plan": plan.as_dict() if plan is not None else None,
                "language_policy": getattr(self, "_language_policy", None).to_dict() if getattr(self, "_language_policy", None) is not None else None,
                "llm_verification_calls": int(getattr(self, "_llm_verify_calls", 0)),
                "llm_query_calls": int(getattr(self, "_llm_query_calls", 0)),
                "broad_search": {
                    "used": bool(getattr(self, "_broad_search_used", False)),
                    "calls": int(getattr(self, "_broad_search_calls", 0)),
                },
                "research_metrics": self._research_metrics(fetched, documents),
            },
            text_content=_render([f for f in findings if f.get("relevant")] or findings),
            exports=[
                Export(
                    id=f"exp_{request.id}_web",
                    type="WEB_EVIDENCE",
                    payload={
                        "findings": findings,
                        "objective": objective,
                        "subjects": subjects,
                        "proposition_coverage": coverage,
                        "retrieval_status": retrieval_code,
                    },
                    contract={"grounding": "page_body", "items": len(findings), "retrieval_status": retrieval_code},
                )
            ],
            provenance={
                "capability": "web_research",
                "request_id": request.id,
                "engines": sorted({f.get("engine", "") for f in findings}),
                "fetched_pages": fetched,
                "search_hits": len(findings) + len(discovery),
                "backend_errors": errors,
                "retrieval_status": retrieval_code,
            },
            references=[
                Reference(
                    ref_type=ReferenceType.WEB_EVIDENCE,
                    ref_id=finding.get("document_id") or f"web:{index}",
                    source=finding["url"],
                    span=" ".join(span.get("text", "") for span in finding.get("evidence_spans", []))[:1000],
                    metadata={"content_hash": finding.get("content_hash", ""), "retrieved_at": finding.get("retrieved_at", "")},
                )
                for index, finding in enumerate(findings)
            ],
            requested_scope=Scope(measure=objective, season=season, game_population=population),
            actual_scope=Scope(
                entities=[str(s) for s in subjects],
                season=season,
                game_population=population,
                date_range=_resolved_date_range(request.inputs.get("resolved_time_scope")),
                measure=objective,
                source="WEB",
                source_coverage=f"{sum(1 for f in findings if f.get('relevant'))} relevant / {len(findings)} retrieved page(s)",
            ),
            confidence="ACCEPTABLE" if any(f.get("grounded") and f.get("relevant") for f in findings) else "WEAK",
            status="PARTIAL",
            need_id=request.need_id,
        )

    def _research_metrics(self, fetched: int, documents: list[dict[str, Any]]) -> dict[str, Any]:
        """Observable research usage for one call (ADR 0024/0028; no enforcement in M1)."""
        outcomes: dict[str, int] = {}
        for document in documents:
            status = str(document.get("retrieval_status") or "UNKNOWN")
            outcomes[status] = outcomes.get(status, 0) + 1
        usage = self._research_usage()
        attempts = list(getattr(self, "_fetch_attempts", []))
        by_type: dict[str, dict[str, int]] = {}
        by_stage: dict[str, int] = {}
        for attempt in attempts:
            source_type = str(attempt.get("source_type") or "UNKNOWN")
            row = by_type.setdefault(source_type, {"attempted": 0, "successful_body": 0})
            row["attempted"] += 1
            row["successful_body"] += int(bool(attempt.get("body_obtained")))
            if attempt.get("body_obtained"):
                stage = str(attempt.get("fetch_method") or "unknown")
                by_stage[stage] = by_stage.get(stage, 0) + 1
        return {
            "provider_calls": sum(self._source_calls.values()),
            "provider_calls_by_source": [
                {"source": name, "calls": count} for name, count in sorted(self._source_calls.items())
            ],
            "tavily_configured": bool(self.tavily_key),
            "tavily_search_calls": self._source_calls.get("tavily", 0),
            "tavily_extract_calls": sum(
                1 for attempt in attempts
                if any(step.startswith("tavily_extract:") for step in attempt.get("fallback_steps", []))
            ),
            "tavily_reported_credits": sum(
                float(row.get("credits") or 0) for row in self._source_outcomes if row.get("source") == "tavily"
            ) + sum(float(attempt.get("provider_credits") or 0) for attempt in attempts),
            "documents_fetched": int(fetched),
            "fetch_outcomes": [
                {"status": status, "count": count} for status, count in sorted(outcomes.items())
            ],
            "fetch_by_source_type": by_type,
            "successful_body_by_method": by_stage,
            "fetch_attempts": attempts,
            "source_outcomes": [dict(item) for item in self._source_outcomes],
            "llm_query_calls": int(getattr(self, "_llm_query_calls", 0)),
            "llm_verify_calls": int(getattr(self, "_llm_verify_calls", 0)),
            "resolver_programs": int(getattr(getattr(self, "_content_resolver", None), "program_count", 0)),
            "resolver_triggers": sum(len(doc.get("resolution_traces") or []) for doc in documents),
            "resolver_outcomes": {
                status: sum(1 for doc in documents for trace in doc.get("resolution_traces") or [] if trace.get("status") == status)
                for status in sorted({trace.get("status", "") for doc in documents for trace in doc.get("resolution_traces") or []})
            },
            "broad_search_used": bool(getattr(self, "_broad_search_used", False)),
            "broad_search_calls": int(getattr(self, "_broad_search_calls", 0)),
            "research_input_tokens": usage["input_tokens"],
            "research_output_tokens": usage["output_tokens"],
            "research_total_tokens": usage["total_tokens"],
            "budget": asdict(GoalResearchBudget()),
        }

    def _research_usage(self) -> dict[str, int | None]:
        records = getattr(self.llm, "usage_records", None) if self.llm else None
        records = list(records[self._usage_start :]) if records else []
        if not records:
            return {"input_tokens": None, "output_tokens": None, "total_tokens": None}
        return {
            "input_tokens": sum(int(r.get("input_tokens") or 0) for r in records),
            "output_tokens": sum(int(r.get("output_tokens") or 0) for r in records),
            "total_tokens": sum(int(r.get("total_tokens") or 0) for r in records),
        }

    def _record_research_diagnostics(self, ctx: Any, artifact: Artifact) -> None:
        diagnostics = getattr(ctx, "diagnostics", None)
        if diagnostics is None or artifact is None:
            return
        metrics = (artifact.structured_data or {}).get("research_metrics")
        if not metrics:
            return
        diagnostics.record("research_metrics", need_id=artifact.need_id, **metrics)

    def _build_query_plan(self, request: ToolRequest, objective: str, language: str, subjects: list[str], user_wording: str) -> ResearchQueryPlan:
        explicit = request.inputs.get("query") or request.inputs.get("search_query")
        terms = [str(t) for t in (request.inputs.get("search_terms") or [])]
        target = str(request.inputs.get("target_information") or "")
        aliases = [str(a) for a in (request.inputs.get("aliases") or [])]
        goal_text = f"{objective} {user_wording} {target} {' '.join(terms)}"
        policy = build_policy(
            user_language=language,
            goal_text=goal_text,
            primary_research_language=(
                next((item for item in build_policy(user_language=language, goal_text=goal_text).research_languages
                      if item != "en"), detect_language(goal_text))
                if self._source_intent in {SourceIntent.COMMUNITY_DISCUSSION, SourceIntent.LOCAL_REPORTING}
                else "en"
            ),
            mandatory_unsupported=False,
            source_hint=target,
        )
        plan = build_query_plan(
            objective=objective,
            subjects=subjects,
            propositions=self._plan_propositions(request),
            user_wording=user_wording,
            aliases=aliases,
            search_terms=terms,
            explicit_query=str(explicit or ""),
            target_information=target,
            research_languages=policy.research_languages,
            expansion_reasons=policy.expansion_reasons,
            source_intent=self._source_intent.value,
        )
        if self.llm and self.llm.configured and not explicit:
            generated = self._generate_queries(objective, request.inputs, subjects, user_wording)
            if generated:
                plan = merge_llm_variants(plan, generated, language=policy.primary_research_language)
        self._language_policy = policy
        return plan

    def _plan_propositions(self, request: ToolRequest) -> list[AnswerProposition]:
        return _parse_propositions(request.inputs.get("propositions"))

    def _candidate_context(
        self,
        plan: ResearchQueryPlan,
        subjects: list[str],
        propositions: list[AnswerProposition],
    ) -> CandidateContext:
        required: set[str] = set()
        season: int | None = None
        population: str | None = None
        for proposition in propositions:
            required |= _required_tokens(proposition)
            if season is None and isinstance(proposition.time_scope, dict) and proposition.time_scope.get("season"):
                season = int(proposition.time_scope["season"])
            population = population or proposition.population
        if season is None:
            for variant in plan.variants:
                if variant.time_scope.get("season"):
                    season = int(variant.time_scope["season"])
                    break
        engine = plan.variants[0].intent if plan.variants else "RELATION"
        return CandidateContext(
            subjects=tuple(subjects),
            required_tokens=frozenset(required),
            season=season,
            population=population,
            freshness=plan.freshness,
            intent=engine,
            discovery_only_engines=frozenset(DISCOVERY_ONLY_ENGINES),
            descriptors=tuple(source.descriptor for source in self.sources),
            source_intent=getattr(self, "_source_intent", SourceIntent.ANALYSIS).value,
        )

    def _generate_queries(self, objective: str, inputs: dict[str, Any], subjects: list[str], user_wording: str) -> list[str]:
        self._llm_query_calls = getattr(self, "_llm_query_calls", 0) + 1
        system = (
            "You write short web search queries. Return JSON {\"queries\":[\"...\"]} with at most 3 queries. "
            "If subject entities are provided, EVERY query MUST begin with the subject entity name(s). "
            "Translate non-English topics into English keywords. For historical questions include the relevant year. "
            "No explanations."
        )
        user = json.dumps(
            {
                "objective": objective,
                "subjects": subjects,
                "user_wording": user_wording,
                "context": inputs.get("search_terms", []),
            },
            ensure_ascii=False,
        )
        try:
            result = self.llm.complete_json(role="planner", system=system, user=user, max_tokens=200)
        except Exception:  # noqa: BLE001
            return []
        return [str(q) for q in result.data.get("queries", []) if isinstance(q, str)][:3]


def _coverage(propositions: list[AnswerProposition], evidence_findings: list[EvidenceFinding]) -> dict[str, str]:
    statuses: dict[str, set[str]] = {}
    for item in evidence_findings:
        statuses.setdefault(item.proposition_id, set()).add(item.status.value)
    coverage: dict[str, str] = {}
    for proposition in propositions:
        seen = statuses.get(proposition.id, set())
        if VerificationStatus.SUPPORTED.value in seen and VerificationStatus.CONTRADICTED.value in seen:
            coverage[proposition.id] = CoverageState.PARTIAL.value
        elif VerificationStatus.CONTRADICTED.value in seen:
            coverage[proposition.id] = CoverageState.CONTRADICTED.value
        elif VerificationStatus.SUPPORTED.value in seen:
            coverage[proposition.id] = CoverageState.SATISFIED.value
        elif VerificationStatus.INSUFFICIENT.value in seen:
            coverage[proposition.id] = CoverageState.UNSUPPORTED.value
        else:
            coverage[proposition.id] = CoverageState.UNSUPPORTED.value
    return coverage


def _is_historical_explanation(propositions: list[AnswerProposition], objective: str) -> bool:
    """True for an EXPLANATION obligation carrying a clearly past year (not a name shortcut)."""
    if not any(proposition.kind == PropositionKind.EXPLANATION for proposition in propositions):
        return False
    text = " ".join(
        [objective, *[proposition.statement for proposition in propositions],
         *[str(proposition.time_scope) for proposition in propositions]]
    )
    current_year = time.gmtime().tm_year
    years = [int(year) for year in re.findall(r"\b(19\d{2}|20\d{2})\b", text)]
    return any(year <= current_year - 1 for year in years)


def _source_plan(propositions: list[AnswerProposition], objective: str,
                 source_intent: SourceIntent = SourceIntent.ANALYSIS) -> dict[str, str]:
    if source_intent in {SourceIntent.INTERVIEW, SourceIntent.PRIMARY_STATEMENT,
                         SourceIntent.COMMUNITY_DISCUSSION, SourceIntent.LOCAL_REPORTING}:
        return {"primary": "intent_relevant_public_source", "reason": source_intent.value.lower()}
    if _is_historical_explanation(propositions, objective):
        return {"primary": "wikipedia", "reason": "historical_explanation"}
    return {"primary": "round_robin", "reason": "default"}


def _apply_source_plan(hits: list, propositions: list[AnswerProposition], objective: str,
                       source_intent: SourceIntent = SourceIntent.ANALYSIS) -> list:
    if source_intent in {SourceIntent.INTERVIEW, SourceIntent.PRIMARY_STATEMENT,
                         SourceIntent.COMMUNITY_DISCUSSION, SourceIntent.LOCAL_REPORTING}:
        return hits
    if not _is_historical_explanation(propositions, objective):
        return hits
    wikipedia = [hit for hit in hits if getattr(hit, "engine", "") == "wikipedia"]
    others = [hit for hit in hits if getattr(hit, "engine", "") != "wikipedia"]
    return wikipedia + others


def _parse_propositions(raw: Any) -> list[AnswerProposition]:
    propositions: list[AnswerProposition] = []
    for item in raw or []:
        if isinstance(item, AnswerProposition):
            propositions.append(item)
        elif isinstance(item, dict):
            try:
                propositions.append(AnswerProposition.model_validate(item))
            except Exception:  # noqa: BLE001 - a malformed proposition must not crash research
                continue
    return propositions


def _record_latency(latency: Any, stage: str, started: float, detail: str = "") -> None:
    if latency is not None and hasattr(latency, "record"):
        latency.record(stage, int((time.monotonic() - started) * 1000), detail)


def _tag_hit(hit: Any, variant: Any, source_id: str) -> None:
    """Attach discovery-facet metadata to a hit (non-invasive; async providers may return
    plain objects). The ranker reads it defensively with ``getattr``."""
    for attribute, value in (
        ("query", getattr(variant, "text", "")),
        ("intent", getattr(variant, "intent", "")),
        ("target_proposition_ids", tuple(getattr(variant, "target_proposition_ids", ()) or ())),
        ("source_id", source_id),
    ):
        try:
            setattr(hit, attribute, value)
        except Exception:  # noqa: BLE001 - tagging must never break retrieval
            return


def _alternate_variant(text: str):
    from app.web.query_plan import QueryVariant

    return QueryVariant(text=text, intent="RELATION")


def _evidence_content_key(text: str) -> str:
    """Canonical identity of an evidence passage for duplicate suppression.

    JSON-shaped spans are compared by parsed value (so a native structured record and its
    derived serialization match); prose is compared whitespace-normalized. This only removes
    exact duplicate candidates for one proposition; it never drops distinct evidence.
    """
    stripped = (text or "").strip()
    if stripped[:1] in {"[", "{"}:
        try:
            import json as _json

            return "json:" + _json.dumps(_json.loads(stripped), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except Exception:  # noqa: BLE001 - not valid JSON; fall through to prose normalization
            pass
    return "text:" + " ".join(stripped.split()).lower()


def _subject_tokens(subjects: list[str]) -> set[str]:
    tokens: set[str] = set()
    for subject in subjects:
        for token in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", subject.lower()):
            if len(token) >= 3:
                tokens.add(token)
    return tokens


def _mentions_subject(title: str, spans: str, subject_tokens: set[str]) -> bool:
    if not subject_tokens:
        return True
    haystack = f"{title} {spans}".lower()
    return sum(1 for token in subject_tokens if token in haystack) >= _MIN_SUBJECT_OVERLAP


def _has_mandatory_support(propositions: list[AnswerProposition], findings: list[EvidenceFinding]) -> bool:
    mandatory = [p for p in propositions if p.mandatory]
    if not mandatory:
        return True
    supported = {f.proposition_id for f in findings if f.status == VerificationStatus.SUPPORTED}
    return all(p.id in supported for p in mandatory)


def _required_hint_tokens(propositions: list[AnswerProposition]) -> set[str]:
    """Union of the requested-relationship tokens across the mandatory propositions.

    Scope/freshness words are already removed by ``relationship_tokens`` so a stray "recent"
    in an unrelated snippet cannot mask a genuine source-coverage gap.
    """
    tokens: set[str] = set()
    for proposition in propositions:
        tokens |= _relationship_tokens(proposition)
    return tokens


def _merge_hits(primary: list, secondary: list) -> list:
    """Merge broad-discovery hits after direct hits, de-duplicated by URL."""
    seen = {str(getattr(hit, "url", "") or "") for hit in primary}
    merged = list(primary)
    for hit in secondary:
        url = str(getattr(hit, "url", "") or "")
        if url and url not in seen:
            seen.add(url)
            merged.append(hit)
    return merged


def _filter_broad_hits(hits: list, subject_tokens: set[str]) -> list:
    """Untrusted broad discovery is admitted only when it names the full subject.

    Direct high-quality sources are not filtered this way. A broad result that shares only a
    common token with the subject (for example an 'Aaron Hicks' page for 'Aaron Judge')
    cannot become a candidate; this protects precision without touching the Evidence Verifier.
    """
    if len(subject_tokens) <= 1:
        return hits
    kept = []
    for hit in hits:
        haystack = f"{getattr(hit, 'title', '') or ''} {getattr(hit, 'snippet', '') or ''}".lower()
        if all(token in haystack for token in subject_tokens):
            kept.append(hit)
    return kept


def _resolved_date_range(scope: Any) -> DateRange | None:
    """Convert the canonical resolved time scope into a concrete ``DateRange``."""
    if not isinstance(scope, dict):
        return None
    window = scope.get("date_range")
    if not isinstance(window, dict):
        return None

    def _parse(value: Any):
        if not value:
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    start, end = _parse(window.get("start")), _parse(window.get("end"))
    if start is None and end is None:
        return None
    return DateRange(start=start, end=end)


def _render(findings: list[dict[str, Any]]) -> str:
    parts = []
    for finding in findings:
        parts.append(f"## {finding['title']} ({finding['url']}, retrieved {finding['retrieved_at']})")
        for span in finding["evidence_spans"]:
            parts.append(f"- {span['text']}")
    return "\n".join(parts)


def _flatten_spans(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The persisted, id-bearing span collection every finding must resolve against."""
    spans: list[dict[str, Any]] = []
    for finding in findings:
        for span in finding.get("evidence_spans", []):
            if isinstance(span, dict):
                spans.append(span)
    return spans
