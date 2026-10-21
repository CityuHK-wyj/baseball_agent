"""Bounded public representations of one discovered source, with honest provenance."""

from __future__ import annotations

import hashlib
import time
import xml.etree.ElementTree as ET
from dataclasses import replace
from urllib.parse import urlparse

from app.capabilities.web_search import FetchedPage, WebSearchConfig, assert_safe_url, extract_text, fetch_page, MIN_PAGE_TEXT

_STOP = {"BLOCKED", "AUTH_REQUIRED", "PAYWALLED", "RATE_LIMITED", "ROBOTS_DISALLOWED"}


def _same_source(original: str, alternate: str) -> bool:
    """Only declared same-host representations are followed automatically."""
    a, b = urlparse(original).hostname or "", urlparse(alternate).hostname or ""
    return a.lower().removeprefix("www.") == b.lower().removeprefix("www.")


def _feed_item(feed_url: str, original_url: str, config: WebSearchConfig) -> FetchedPage:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    started = time.monotonic()
    try:
        from app.web.transport import request
        response = request('GET',feed_url,config=config,headers={'User-Agent':config.user_agent})
        if response.status_code in {401, 402, 403, 429}:
            code = {401: "AUTH_REQUIRED", 402: "PAYWALLED", 403: "BLOCKED", 429: "RATE_LIMITED"}[response.status_code]
            return FetchedPage(original_url, feed_url, "", "", now, False, error_code=code,
                               retrieval_method="rss_item", status_code=response.status_code)
        response.raise_for_status()
        acquisition = response.extensions.get('acquisition',{})
        raw = response.content
        root = ET.fromstring(raw)
        target = original_url.rstrip("/")
        for item in root.iter():
            if item.tag.rsplit("}", 1)[-1] not in {"item", "entry"}:
                continue
            links = [node.text or node.get("href") or "" for node in item if node.tag.rsplit("}", 1)[-1] == "link"]
            if not any(link.rstrip("/") == target for link in links):
                continue
            bodies = [node.text or "" for node in item if node.tag.rsplit("}", 1)[-1] in {"encoded", "content", "summary", "description"}]
            title = next((node.text or "" for node in item if node.tag.rsplit("}", 1)[-1] == "title"), "")
            body = max((extract_text(value.encode(), "text/html")[1] for value in bodies), key=len, default="")
            if len(body) >= MIN_PAGE_TEXT:
                return FetchedPage(original_url, feed_url, title, body, now, True,
                                   content_hash=hashlib.sha256(raw).hexdigest(), retrieval_method="rss_item",
                                   canonical_url=original_url, status_code=response.status_code,
                                   raw_content=raw, content_type="application/xml", acquisition=acquisition,
                                   elapsed_ms=round((time.monotonic()-started)*1000))
        return FetchedPage(original_url, feed_url, "", "", now, False, error_code="NO_BODY",
                           retrieval_method="rss_item", status_code=response.status_code,
                           raw_content=raw, content_type="application/xml", acquisition=acquisition)
    except Exception as exc:
        return FetchedPage(original_url, feed_url, "", "", now, False, error=type(exc).__name__,
                           error_code="FETCH_ERROR", retrieval_method="rss_item", acquisition=getattr(exc,'acquisition',{}))


def render_public_page(url: str, config: WebSearchConfig) -> FetchedPage:
    """Active browser networking is unavailable until it obeys the shared hard bounds.

    A validate-then-continue browser route resolves again at connect time and cannot
    enforce compressed/raw/body limits. Do not expose that optional security escape.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return FetchedPage(url, url, '', '', now, False, error='bounded browser transport unavailable',
                       error_code='JS_RENDER_REQUIRED', retrieval_method='browser')


def fetch_with_fallback(url: str, config: WebSearchConfig, *, browser_enabled: bool = False,
                        fetch=fetch_page, api_extract=None) -> FetchedPage:
    """HTTP, declared same-source representations, then optional public rendering."""
    first = fetch(url, config)
    last = first
    steps = [f"http:{first.error_code or 'SUCCESS'}"]
    if first.ok or first.error_code in _STOP:
        return replace(first, fallback_steps=tuple(steps))
    candidates = [first.canonical_url, *first.alternate_urls]
    for alternate in dict.fromkeys(candidates[:3]):
        if not alternate or alternate == first.final_url or not _same_source(first.final_url, alternate):
            continue
        if alternate.lower().endswith((".rss", ".atom", ".xml")) or "feed" in urlparse(alternate).path.lower():
            page = _feed_item(alternate, first.final_url, config)
        else:
            page = fetch(alternate, config)
            page = replace(page, url=url, canonical_url=first.canonical_url or first.final_url,
                           retrieval_method="alternate_html")
        steps.append(f"{page.retrieval_method}:{page.error_code or 'SUCCESS'}")
        last = page
        if page.ok or page.error_code in _STOP:
            return replace(page, fallback_steps=tuple(steps))
    if browser_enabled and first.error_code == "JS_RENDER_REQUIRED":
        page = render_public_page(first.final_url, config)
        steps.append(f"browser:{page.error_code or 'SUCCESS'}")
        last = page
        if page.ok or page.error_code in _STOP:
            return replace(page, url=url, fallback_steps=tuple(steps))
    if api_extract is not None and first.error_code in {"JS_RENDER_REQUIRED", "NO_BODY", "FETCH_ERROR"}:
        page = api_extract(url, config)
        steps.append(f"{page.retrieval_method}:{page.error_code or 'SUCCESS'}")
        last = page
        if page.ok or page.error_code in _STOP:
            return replace(page, url=url, fallback_steps=tuple(steps))
    return replace(last, url=url, fallback_steps=tuple(steps))
