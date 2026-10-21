"""Test-only typed evidence-assessor fixtures (WSA-04).

WSA-04 makes the deterministic verifier fail closed when no semantic assessor is available:
required-token overlap is candidate eligibility, never entailment. Downstream-mechanism tests
that used to rely on the no-LLM path now inject a *competent* assessor fixture instead.

These fixtures read the verifier/assessor payload and propose a typed proposition relation.
They are intentionally conservative: an ``ENTAILS`` proposal is only returned when the passage
actually carries the requested relationship tokens. They are never imported by production code.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

_TOKEN_RE = re.compile(r"[a-z][a-z0-9'\-]+")

# Production marker (see app/verification/evidence_semantics.SEMANTIC_CONFORMANCE_SYSTEM).
REVIEWER_MARKER = "semantic grounding conformance reviewer"


def reviewer_reply(system: str) -> SimpleNamespace | None:
    """Answer the WSA-09 semantic conformance reviewer for test doubles."""
    if REVIEWER_MARKER in (system or ""):
        return SimpleNamespace(data={"supported": True, "reason": "fixture"})
    return None


def grounding_receipts(passage: str, *, fields: tuple[str, ...] = ("RELATION", "SOURCE_ROLE")) -> list[dict]:
    """A grounded receipt quoting the supplied passage for the requested authority fields."""
    quote = " ".join((passage or "").split())
    if len(quote) < 8:
        return []
    quote = quote[:200]
    return [
        {"field": field, "source_quote": quote, "locator": "SPAN",
         "basis_kind": "SOURCE_QUOTE", "rationale": "fixture quote"}
        for field in fields
    ]


def passage_from_user(user: str) -> str:
    """Read the evidence passage from either verifier (``passage``) or assessment (``evidence.span``) packets."""
    try:
        payload = json.loads(user or "{}")
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(payload, dict):
        return ""
    if payload.get("passage"):
        return str(payload["passage"])
    evidence = payload.get("evidence")
    if isinstance(evidence, dict):
        return str(evidence.get("span") or evidence.get("context_before") or "")
    return ""


def _inflection_roots(token: str) -> set[str]:
    forms = {token}
    for suffix in ("ing", "ed", "es", "s", "d"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            forms.add(token[: -len(suffix)])
    return forms


def mentions(haystack: str, token: str) -> bool:
    if not token:
        return False
    lowered = (haystack or "").lower()
    for form in _inflection_roots(token.lower()):
        if re.search(
            rf"(?<![a-z0-9]){re.escape(form)}(?:s|es|d|ed|ing)?(?![a-z0-9])", lowered
        ) is not None:
            return True
    return False


class FixtureAssessorLLM:
    """A typed assessor fake usable by both ``verify_span`` and ``WebResearchTool``.

    ``complete_json`` dispatches on the payload/role so the same fixture can serve the web
    query planner (empty variant list), the evidence verifier and the Judge drill-down.
    """

    configured = True

    def __init__(
        self,
        *,
        relation: str = "ENTAILS",
        strength: str = "ASSERTED",
        status: str = "SUPPORTED",
        temporal_relevance: str = "CURRENT_STATE",
        failure: BaseException | None = None,
        strict_tokens: bool = True,
        source_class: str = "NEWS",
        content_role: str = "REPORTING",
        evidential_basis: str = "SECONDARY",
    ) -> None:
        self.relation = relation
        self.strength = strength
        self.status = status
        self.temporal_relevance = temporal_relevance
        self.failure = failure
        self.strict_tokens = strict_tokens
        self.source_class = source_class
        self.content_role = content_role
        self.evidential_basis = evidential_basis
        self.usage_records: list = []

    def _payload(self, user: str) -> dict:
        try:
            value = json.loads(user or "{}")
            return value if isinstance(value, dict) else {}
        except Exception:  # noqa: BLE001 - a malformed fixture payload is treated as empty
            return {}

    def complete_json(self, *, role: str = "response", system: str = "", user: str = "", **kwargs):
        if self.failure is not None:
            raise self.failure
        payload = self._payload(user)
        if role == "planner":
            return SimpleNamespace(data={"queries": []})
        reviewer = reviewer_reply(system)
        if reviewer is not None:
            return reviewer
        proposition = payload.get("proposition") or {}
        evidence = payload.get("evidence") or {}
        passage = str(payload.get("passage") or evidence.get("span") or "")
        required = str(proposition.get("required_information") or "")
        subject = str(proposition.get("subject") or "")
        if not subject and passage:
            # A parsed WebResearchTool proposition may omit the subject; the verifier requires a
            # non-empty subject that actually appears in the passage. Derive one from the prose.
            match = re.search(r"\b([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+)+)", passage)
            subject = match.group(1) if match else (passage.split()[0] if passage.split() else "")
        statement = str(proposition.get("statement") or "")
        if self.relation == "ENTAILS" and self.strict_tokens:
            required_tokens = _TOKEN_RE.findall(required.lower())
            if required_tokens and not any(mentions(passage, token) for token in required_tokens):
                return SimpleNamespace(data={
                    "status": "INSUFFICIENT",
                    "relation": "UNCERTAIN",
                    "assertion_strength": "UNKNOWN",
                    "temporal_relevance": "UNKNOWN",
                    "fact": {},
                })
        value = ""
        for number in re.findall(r"\d+(?:\.\d+)?", statement):
            candidates = [number]
            if "." in number:
                candidates.append(number.rstrip("0").rstrip("."))
            for candidate in candidates:
                if candidate and candidate in passage:
                    value = candidate
                    break
            if value:
                break
        scope_parts: list[str] = []
        time_scope = proposition.get("time_scope") or {}
        if isinstance(time_scope, dict) and time_scope.get("season"):
            season = str(time_scope["season"])
            if season in passage:
                scope_parts.append(season)
        population = str(proposition.get("population") or "")
        if population and population.lower() in passage.lower():
            scope_parts.append(population.lower())
        scope = " ".join(scope_parts)
        return SimpleNamespace(data={
            "status": self.status,
            "establishes": "fixture evidence assessment",
            "relation": self.relation,
            "assertion_strength": self.strength,
            "temporal_relevance": self.temporal_relevance,
            "source_role": {
                "source_class": self.source_class,
                "content_role": self.content_role,
                "evidential_basis": self.evidential_basis,
            },
            "polarity": "AFFIRMED" if self.relation == "ENTAILS" else "UNKNOWN",
            "event_or_state_time": "",
            "attribution": "",
            "caveats": [],
            "grounding": grounding_receipts(passage),
            "fact": {"subject": subject, "metric": required, "value": value, "scope": scope},
            "rationale": "fixture",
        })

    def complete_text(self, **kwargs):
        return ("fixture answer", 1, "fixture")


class _SupportLLM:
    """A minimal fixed assessor for direct ``verify_span`` tests."""

    configured = True

    def __init__(self, subject: str, metric: str, value: str, scope: str):
        self.subject, self.metric, self.value, self.scope = subject, metric, value, scope
        self.usage_records: list = []

    def complete_json(self, *, role: str = "response", system: str = "", user: str = "", **kwargs):
        reviewer = reviewer_reply(system)
        if reviewer is not None:
            return reviewer
        passage = passage_from_user(user)
        return SimpleNamespace(data={
            "status": "SUPPORTED",
            "relation": "ENTAILS",
            "assertion_strength": "ASSERTED",
            "temporal_relevance": "CURRENT_STATE",
            "source_role": {"source_class": "NEWS", "content_role": "REPORTING", "evidential_basis": "SECONDARY"},
            "grounding": grounding_receipts(passage) or [],
            "fact": {"subject": self.subject, "metric": self.metric, "value": self.value, "scope": self.scope},
        })


def support_llm(
    subject: str = "Shohei Ohtani",
    metric: str = "home runs",
    value: str = "",
    scope: str = "2024",
) -> _SupportLLM:
    return _SupportLLM(subject, metric, value, scope)


class FixedAssessor:
    """A fixed-data assessor double that answers the reviewer and emits grounding receipts."""

    configured = True

    def __init__(self, data: dict):
        self.data = dict(data)
        self.usage_records: list = []

    def complete_json(self, *, role: str = "response", system: str = "", user: str = "", **kwargs):
        reply = reviewer_reply(system)
        if reply is not None:
            return reply
        data = dict(self.data)
        if "grounding" not in data:
            data["grounding"] = grounding_receipts(passage_from_user(user))
        return SimpleNamespace(data=data)
