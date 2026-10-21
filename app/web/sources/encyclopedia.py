"""Keyless Wikipedia source adapter (M1-T4, ADR 0026).

Wikipedia is the single most important correction to the Phase 5B baseline: it is live
again and is the natural home for historical/explanatory research. Availability is probed
at runtime and returns a typed outcome; it is never assumed from a stale audit.
"""

from __future__ import annotations

from app.capabilities.web_search import FetchedPage, WebSearchConfig, WikipediaSearchBackend, fetch_page
from app.web.outcomes import SourceOutcome, classify_search_result
from app.web.sources.base import SourceDescriptor

_WIKIPEDIA_DESCRIPTOR = SourceDescriptor(
    source_id="wikipedia",
    kind="ENCYCLOPEDIA",
    discovery=True,
    full_body=True,
    structured=False,
    historical=True,
    current=True,
    auth="none",
    trust_weight=0.85,
    latency_class="medium",
    rate_limit_per_min=None,
)


class WikipediaSource:
    """Search + body fetch + a runtime capability probe for Wikipedia."""

    name = "wikipedia"

    def __init__(self, config: WebSearchConfig | None = None):
        self.config = config or WebSearchConfig()
        self.descriptor = _WIKIPEDIA_DESCRIPTOR
        self._backend = WikipediaSearchBackend(self.config)
        self.last_error = ""

    def search(self, query: str, *, limit: int = 3, language: str = "en") -> list:
        hits = self._backend.search(query, limit=limit, language=language)
        self.last_error = self._backend.last_error
        return hits

    def fetch(self, url: str) -> FetchedPage:
        return fetch_page(url, self.config)

    def available(self, *, language: str = "en") -> SourceOutcome:
        """Probe the source; return a typed outcome instead of raising when unreachable."""
        try:
            hits = self._backend.search("Major League Baseball", limit=1, language=language)
        except Exception as exc:  # noqa: BLE001 - a probe failure is a typed outcome
            return classify_search_result(hits=[], error="", raised=exc)
        return classify_search_result(hits=hits, error=self._backend.last_error)
