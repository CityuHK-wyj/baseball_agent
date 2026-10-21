"""Optional authenticated discovery and extracted representation from Tavily.

Search `content` remains a discovery snippet. Only a separate Extract response with
`raw_content` can become a document body, labeled as an API representation.
"""

from __future__ import annotations

import hashlib
import time

from app.capabilities.web_search import FetchedPage, SearchHit, WebSearchConfig, assert_safe_url, MIN_PAGE_TEXT
from app.web.sources.base import SourceDescriptor
from app.web.transport import request

_ROOT = "https://api.tavily.com"


def _credits(payload: dict) -> float | None:
    """Retain only numeric usage; never persist a provider response wholesale."""
    usage = payload.get("usage") or {}
    value = usage.get("credits") if isinstance(usage, dict) else None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_failure(message: str) -> str:
    lowered = (message or "").lower()
    if "unsupported" in lowered or "invalid url" in lowered:
        return "UNSUPPORTED_URL"
    if "access denied" in lowered or "forbidden" in lowered or "blocked" in lowered:
        return "SOURCE_INACCESSIBLE"
    return "NO_BODY"


class TavilySearchBackend:
    name = "tavily"
    descriptor = SourceDescriptor(source_id="tavily", kind="BROAD_SEARCH", discovery=True,
                                  full_body=True, structured=False, historical=True, current=True,
                                  auth="api_key", trust_weight=0.25)

    def __init__(self, key: str, config: WebSearchConfig):
        self.key = key
        self.config = config
        self.last_error = ""
        self.last_usage_credits: float | None = None

    def search(self, query: str, *, limit: int = 5, language: str = "en") -> list[SearchHit]:
        import httpx
        self.last_error = ""
        self.last_usage_credits = None
        if not self.key:
            self.last_error = "AUTH_REQUIRED: TAVILY_API_KEY missing"
            return []
        try:
            response = request('POST',f"{_ROOT}/search",config=self.config, headers={"Authorization": f"Bearer {self.key}"},
                                  json={"query": query, "max_results": limit, "search_depth": "basic",
                                        "include_raw_content": False, "include_usage": True,
                                        "language": language}, timeout=self.config.timeout)
            if response.status_code != 200:
                self.last_error = f"HTTP {response.status_code}"
                return []
            payload = response.json()
            self.last_usage_credits = _credits(payload)
            return [SearchHit(title=str(item.get("title") or ""), url=str(item.get("url") or ""),
                              snippet=str(item.get("content") or "")[:300], engine=self.name)
                    for item in payload.get("results", [])[:limit] if item.get("url")]
        except Exception as exc:
            self.last_error = type(exc).__name__  # never log the request or bearer token
            return []


def extract_representation(url: str, key: str, config: WebSearchConfig) -> FetchedPage:
    import httpx
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not key:
        return FetchedPage(url, url, "", "", now, False, error_code="AUTH_REQUIRED",
                           retrieval_method="tavily_extract")
    started = time.monotonic()
    try:
        assert_safe_url(url)
        response = request('POST',f"{_ROOT}/extract",config=config, headers={"Authorization": f"Bearer {key}"},
                              json={"urls": [url], "extract_depth": "basic", "include_images": False,
                                    "include_usage": True},
                              timeout=config.timeout)
        if response.status_code != 200:
            code = {401: "AUTH_REQUIRED", 402: "QUOTA_EXCEEDED", 403: "BLOCKED", 429: "RATE_LIMITED"}.get(response.status_code, "FETCH_ERROR")
            return FetchedPage(url, url, "", "", now, False, error_code=code,
                               retrieval_method="tavily_extract", status_code=response.status_code)
        payload = response.json()
        credits = _credits(payload)
        item = next((item for item in payload.get("results", []) if item.get("url") == url), None)
        failed = next((item for item in payload.get("failed_results", []) if item.get("url") == url), None)
        body = str((item or {}).get("raw_content") or "")[:config.max_bytes]
        if len(body) < MIN_PAGE_TEXT:
            return FetchedPage(url, url, "", "", now, False,
                               error_code=_extract_failure(str((failed or {}).get("error") or "")),
                               retrieval_method="tavily_extract", status_code=response.status_code,
                               provider_credits=credits)
        return FetchedPage(url, url, "", body, now, True, content_hash=hashlib.sha256(body.encode()).hexdigest(),
                           retrieval_method="tavily_extract", canonical_url=url, status_code=response.status_code,
                           raw_content=body.encode("utf-8"), content_type="text/plain",
                           elapsed_ms=round((time.monotonic()-started)*1000), provider_credits=credits)
    except Exception as exc:
        return FetchedPage(url, url, "", "", now, False, error=type(exc).__name__, error_code="FETCH_ERROR",
                           retrieval_method="tavily_extract")
