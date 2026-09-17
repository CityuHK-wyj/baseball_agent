"""First-class web research seams.

Web is a *recovery* path, not an afterthought: when local data is missing, stale or
unknown, the Planner may issue a free-form web research objective. This module defines
the provider-agnostic search seam plus a fetcher that the existing ``WebEvidenceTool``
can consume.

Live search requires an external capability/API. The default backend is explicitly
disabled until an endpoint and key are configured, so the runtime never pretends that
unknown -> web works. ``StaticSearchBackend`` exists for deterministic tests and offline
demonstrations.
"""

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Protocol

from app.models.evidence import RawWebResult

DEFAULT_MAX_RESULTS = 5


class SearchBackend(Protocol):
    """Provider-agnostic web search. Returns unstructured documents, never trusted facts."""

    def search(self, query: str, *, max_results: int = DEFAULT_MAX_RESULTS
               ) -> tuple[RawWebResult, ...]: ...


class StaticSearchBackend:
    """Deterministic backend for tests and offline demos: query -> fixed documents."""

    def __init__(self, documents: dict[str, tuple[RawWebResult, ...]] | None = None,
                 default: tuple[RawWebResult, ...] = ()) -> None:
        self._documents = dict(documents or {})
        self._default = tuple(default)

    def search(self, query: str, *, max_results: int = DEFAULT_MAX_RESULTS
               ) -> tuple[RawWebResult, ...]:
        matched = ()
        for key, value in self._documents.items():
            if key.casefold() in query.casefold() or query.casefold() in key.casefold():
                matched = value
                break
        return (matched or self._default)[:max_results]


class HttpJsonSearchBackend:
    """A minimal configurable JSON search backend.

    It deliberately does not hard-code a vendor. Configure ``endpoint`` and ``api_key``
    (for example via environment variables surfaced by :mod:`app.config`) and it posts a
    ``{"query": ..., "max_results": ...}`` body and expects a JSON list of
    ``{title, url, text, source}`` objects. Without an endpoint it raises so callers can
    report the exact blocker instead of silently returning nothing.
    """

    def __init__(self, endpoint: str | None = None, api_key: str | None = None,
                 timeout: float = 15.0, transport: Callable[[str, dict], object] | None = None
                 ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self._endpoint and (self._api_key or self._transport is not None))

    def search(self, query: str, *, max_results: int = DEFAULT_MAX_RESULTS
               ) -> tuple[RawWebResult, ...]:
        if not self.configured:
            raise RuntimeError(
                "web search is not configured: set WEB_SEARCH_ENDPOINT and WEB_SEARCH_API_KEY")
        documents = self._transport_call({"query": query, "max_results": max_results})
        results: list[RawWebResult] = []
        for index, item in enumerate(documents if isinstance(documents, list) else []):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or item.get("link") or "")
            if not url:
                continue
            results.append(RawWebResult(
                result_id=f"web-{abs(hash((query, url))) & 0xFFFFFFFF}",
                url=url, title=str(item.get("title") or ""),
                text=str(item.get("text") or item.get("snippet") or item.get("content") or ""),
                source=str(item.get("source") or "web"),
                retrieved_at=datetime.now(timezone.utc)))
        return tuple(results[:max_results])

    def _transport_call(self, body: dict):
        if self._transport is not None:
            return self._transport(self._endpoint, body)
        import json
        import urllib.request
        request = urllib.request.Request(
            self._endpoint, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self._api_key}"} if self._api_key else {})})
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            return json.loads(response.read().decode())


class WebResearchFetcher:
    """Adapt a :class:`SearchBackend` to the single-document ``fetcher`` seam.

    The objective and search hints written by the Planner are free-form; they are used to
    build the search query verbatim. This is where open-world planning meets web recovery
    without either side inventing SQL or physical identifiers.
    """

    def __init__(self, backend: SearchBackend, *, max_results: int = DEFAULT_MAX_RESULTS) -> None:
        self._backend = backend
        self._max_results = max_results

    def __call__(self, task) -> RawWebResult:
        queries = self._queries(task)
        query = queries[0] if queries else getattr(task, "description", "")
        results = self._backend.search(query, max_results=self._max_results)
        timestamp = datetime.now(timezone.utc)
        if not results:
            return RawWebResult(result_id=f"web-{abs(hash(query)) & 0xFFFFFFFF}", url="web://none",
                                title=f"No web results for: {query}", text="", source="web",
                                retrieved_at=timestamp)
        title = results[0].title or query
        url = results[0].url
        text = "\n\n".join(
            f"[{item.title}] ({item.url})\n{item.text}" for item in results if item.text)
        return RawWebResult(result_id=f"web-{abs(hash((query, url))) & 0xFFFFFFFF}", url=url,
                            title=title, text=text, source=results[0].source, retrieved_at=timestamp)

    @staticmethod
    def _queries(task) -> tuple[str, ...]:
        hints = tuple(getattr(task, "search_hints", ()) or ())
        if hints:
            return hints
        objective = getattr(task, "objective", "") or ""
        instructions = getattr(task, "instructions", "") or ""
        description = getattr(task, "description", "")
        combined = " ".join(part for part in (objective, instructions, description) if part)
        return (combined.strip(),) if combined.strip() else ()

    @staticmethod
    def combined(results: Iterable[RawWebResult]) -> RawWebResult:
        items = tuple(results)
        if not items:
            raise ValueError("no web results to combine")
        return RawWebResult(
            result_id=f"web-combined-{abs(hash(tuple(item.url for item in items))) & 0xFFFFFFFF}",
            url=items[0].url, title=items[0].title,
            text="\n\n".join(item.text for item in items if item.text),
            source=items[0].source, retrieved_at=items[0].retrieved_at)
