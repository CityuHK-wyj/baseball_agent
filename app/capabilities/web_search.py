"""Web infrastructure: SSRF guard, search backends, bounded page fetch and extraction.

A search hit (snippet) is never grounded support; only a retrieved page body can ground a
claim. Every fetched page keeps its URL, title, retrieval time, content hash and text
spans (with offsets), so evidence can be inspected and reproduced.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse
from app.web.transport import request as safe_request


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    engine: str = ""


@dataclass
class FetchedPage:
    url: str
    final_url: str
    title: str
    text: str
    retrieved_at: str
    ok: bool
    error: str = ""
    error_code: str = ""
    content_hash: str = ""
    publication_date: str | None = None
    status_code: int | None = None
    retrieval_method: str = "http"
    fallback_steps: tuple[str, ...] = ()
    canonical_url: str = ""
    alternate_urls: tuple[str, ...] = ()
    elapsed_ms: int = 0
    provider_credits: float | None = None
    # Content-artifact retention (ADR 0031): the acquired bytes and their declared media
    # type, so an original representation can be preserved and text/structured views derived
    # with traceable lineage. Defaults keep every existing constructor valid.
    raw_content: bytes = b""
    content_type: str = ""
    acquisition: dict[str, Any] = field(default_factory=dict)


@dataclass
class WebSearchConfig:
    search_endpoint: str = ""
    search_api_key: str = ""
    user_agent: str = "BaseballAgent/0.1"
    timeout: float = 15.0
    max_bytes: int = 2_000_000


# --------------------------------------------------------------------------- SSRF

_BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".home", ".lan")

DEFAULT_FEEDS = [
    "https://www.mlb.com/feeds/news/rss.xml",
    "https://blogs.fangraphs.com/feed/",
    "https://www.cbssports.com/rss/headlines/mlb/",
    "https://sports.yahoo.com/mlb/rss/",
]

# A retrieved page must carry this much text to count as grounded evidence.
MIN_PAGE_TEXT = 200

# Engines whose links cannot be fetched into a page body (discovery only).
DISCOVERY_ONLY_ENGINES = {"google_news"}

# Some sandboxed/NATed environments (e.g. WSL2) route all egress through a transparent
# proxy that resolves public hostnames into the RFC 2544 benchmarking range. That range is
# non-routable and cannot address internal services, so it is allowed while genuine
# private/loopback/link-local ranges remain blocked. Operators can extend this list.
PROXY_EGRESS_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("198.18.0.0/15"),
)


def _is_allowed_egress(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    for network in PROXY_EGRESS_NETWORKS:
        if ip.version == network.version and ip in network:
            return True
    return False


class SSRFError(ValueError):
    pass


def _validated_addresses(url: str) -> tuple[str, int, list[str]]:
    """Validate scheme/host/port and DNS, returning every address that must be pinned."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"scheme '{parsed.scheme}' is not allowed")
    host = parsed.hostname or ""
    if not host:
        raise SSRFError("missing host")
    lowered = host.lower()
    if lowered in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or lowered.endswith(_BLOCKED_HOST_SUFFIXES):
        raise SSRFError(f"host '{host}' is not allowed")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in (80, 443):
        raise SSRFError(f"port {port} is not allowed")
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SSRFError(f"could not resolve host: {exc}") from exc
    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise SSRFError(f"unresolvable address '{address}'") from exc
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise SSRFError(f"address '{address}' is not allowed")
        if ip.is_private and not _is_allowed_egress(ip):
            raise SSRFError(f"address '{address}' is not allowed")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise SSRFError(f"host '{host}' resolved to no usable address")
    return host, port, addresses


def assert_safe_url(url: str) -> str:
    _validated_addresses(url)
    return url


def _pinned_target(url: str) -> tuple[str, str, dict[str, str], dict[str, Any]]:
    """Resolve once and return a request pinned to a validated address.

    The connection dials the validated IP literal, so no second resolver call can
    rebind the host between the safety check and the socket. The original hostname is
    preserved in the Host header and as the TLS server/SNI name.
    """
    parsed = urlparse(url)
    host, port, addresses = _validated_addresses(url)
    address = addresses[0]
    literal = f"[{address}]" if ":" in address else address
    default_port = 443 if parsed.scheme == "https" else 80
    authority = literal if port == default_port else f"{literal}:{port}"
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    pinned = f"{parsed.scheme}://{authority}{path}"
    host_header = host if port == default_port else f"{host}:{port}"
    headers = {"Host": host_header}
    extensions: dict[str, Any] = {}
    if parsed.scheme == "https":
        # httpcore uses this for both SNI and certificate hostname verification.
        extensions["sni_hostname"] = host
    return pinned, host, headers, extensions


# ------------------------------------------------------------------------- search


class SearchBackend:
    name = "base"
    # Non-empty after a search() call whose transport failed. This distinguishes a dead
    # backend from a backend that genuinely returned no results (ADR 0017).
    last_error = ""

    def search(self, query: str, *, limit: int = 5, language: str = "en") -> list[SearchHit]:  # pragma: no cover
        raise NotImplementedError


class EndpointSearchBackend(SearchBackend):
    """Generic JSON search endpoint. Response: {"results":[{"title","url","snippet"}]}."""

    name = "endpoint"

    def __init__(self, endpoint: str, api_key: str, config: WebSearchConfig):
        self.endpoint = endpoint
        self.api_key = api_key
        self.config = config

    def search(self, query: str, *, limit: int = 5, language: str = "en") -> list[SearchHit]:
        import httpx

        self.last_error = ""
        headers = {"User-Agent": self.config.user_agent}
        params = {"q": query, "limit": limit}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = safe_request('GET', self.endpoint, config=self.config, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []
        hits: list[SearchHit] = []
        for entry in payload.get("results", [])[:limit]:
            url = entry.get("url") or entry.get("link") or ""
            if not url:
                continue
            hits.append(SearchHit(title=entry.get("title", ""), url=url, snippet=entry.get("snippet", entry.get("content", "")), engine=self.name))
        return hits


class GoogleNewsRSSBackend(SearchBackend):
    name = "google_news"

    def __init__(self, config: WebSearchConfig):
        self.config = config

    def search(self, query: str, *, limit: int = 5, language: str = "en") -> list[SearchHit]:
        import httpx

        self.last_error = ""
        if language.startswith("zh"):
            params = {"q": query, "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"}
        elif language.startswith("ja"):
            params = {"q": query, "hl": "ja", "gl": "JP", "ceid": "JP:ja"}
        elif language.startswith("ko"):
            params = {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
        elif language.startswith("es"):
            params = {"q": query, "hl": "es", "gl": "MX", "ceid": "MX:es-419"}
        else:
            params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        url = "https://news.google.com/rss/search"
        try:
            response = safe_request('GET',
                url, config=self.config,
                params=params,
                headers={"User-Agent": self.config.user_agent},
                timeout=self.config.timeout,
                follow_redirects=True,
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []
        hits: list[SearchHit] = []
        for item in root.iter("item"):
            link = (item.findtext("link") or "").strip()
            title = (item.findtext("title") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            source = item.find("source")
            publisher = source.text if source is not None and source.text else ""
            if not link:
                continue
            snippet = f"{publisher} · {pub}".strip(" ·")
            hits.append(SearchHit(title=title, url=link, snippet=snippet, engine=self.name))
            if len(hits) >= limit:
                break
        return hits


class WikipediaSearchBackend(SearchBackend):
    name = "wikipedia"

    def __init__(self, config: WebSearchConfig):
        self.config = config

    def search(self, query: str, *, limit: int = 3, language: str = "en") -> list[SearchHit]:
        import httpx

        self.last_error = ""
        lang = "zh" if language.startswith("zh") else "en"
        api = f"https://{lang}.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": limit,
        }
        try:
            response = safe_request('GET', api, config=self.config, params=params, headers={"User-Agent": self.config.user_agent})
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []
        hits: list[SearchHit] = []
        for entry in payload.get("query", {}).get("search", [])[:limit]:
            title = entry.get("title", "")
            page_url = f"https://{lang}.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}"
            snippet = re.sub(r"<[^>]+>", "", entry.get("snippet", ""))
            hits.append(SearchHit(title=title, url=page_url, snippet=snippet, engine=self.name))
        return hits


def build_backends(config: WebSearchConfig, feeds: list[str] | None = None) -> list[SearchBackend]:
    backends: list[SearchBackend] = []
    if config.search_endpoint:
        backends.append(EndpointSearchBackend(config.search_endpoint, config.search_api_key, config))
    # Grounded, direct-URL sources first; discovery-only engines last.
    backends.append(FeedSearchBackend(config, feeds))
    backends.append(WikipediaSearchBackend(config))
    backends.append(GoogleNewsRSSBackend(config))
    return backends


def build_broad_backends(config: WebSearchConfig) -> list[SearchBackend]:
    """Real broad-web discovery, used only as an escalation when direct sources are thin."""
    return [BroadSearchBackend(config)]


class BroadSearchBackend(SearchBackend):
    """Keyless broad-web discovery via public search HTML (Bing, then Brave fallback).

    This is discovery only: results are candidate URLs that must still be fetched into a
    document body before they can ground a claim. It never becomes trusted evidence by
    itself. The provider is a genuine live surface; when it is blocked or unavailable the
    backend records ``last_error`` so the caller degrades honestly instead of reporting
    ``NO_RESULTS``.
    """

    name = "broad_search"
    _BROWSER_UA = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    )

    def __init__(self, config: WebSearchConfig):
        self.config = config

    def search(self, query: str, *, limit: int = 5, language: str = "en") -> list[SearchHit]:
        import httpx

        self.last_error = ""
        headers = {"User-Agent": self._BROWSER_UA, "Accept-Language": _accept_language(language)}
        for fetch in (self._brave, self._bing):
            try:
                hits = fetch(query, limit, language, headers, httpx)
            except Exception as exc:  # noqa: BLE001 - a provider must never crash research
                self.last_error = f"{type(exc).__name__}: {exc}"
                continue
            if hits:
                return hits
        return []

    def _bing(self, query, limit, language, headers, httpx) -> list[SearchHit]:
        from bs4 import BeautifulSoup

        params = {"q": query, "count": max(10, limit)}
        locale = _bing_locale(language)
        if locale:
            params.update(locale)
        response = safe_request('GET', "https://www.bing.com/search", config=self.config, params=params, headers=headers,
            timeout=self.config.timeout, follow_redirects=True,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        hits: list[SearchHit] = []
        for block in soup.select("li.b_algo"):
            anchor = block.find("a", href=True)
            if anchor is None:
                continue
            url = _decode_bing_url(anchor["href"])
            title = anchor.get_text(" ", strip=True)
            if not url or not title:
                continue
            caption = block.select_one("div.b_caption, p")
            snippet = caption.get_text(" ", strip=True) if caption is not None else ""
            hits.append(SearchHit(title=title, url=url, snippet=snippet, engine=self.name))
            if len(hits) >= limit:
                break
        return hits

    def _brave(self, query, limit, language, headers, httpx) -> list[SearchHit]:
        from bs4 import BeautifulSoup

        params = {"q": query}
        response = safe_request('GET', "https://search.brave.com/search", config=self.config, params=params, headers=headers,
            timeout=self.config.timeout, follow_redirects=True,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        hits: list[SearchHit] = []
        for block in soup.select("div.snippet"):
            anchor = block.find("a", href=True)
            if anchor is None or not str(anchor["href"]).startswith("http"):
                continue
            title = anchor.get_text(" ", strip=True)
            url = str(anchor["href"])
            if not title:
                continue
            snippet_node = block.select_one("div.snippet-description, div.snippet-content")
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node is not None else ""
            hits.append(SearchHit(title=title, url=url, snippet=snippet, engine=self.name))
            if len(hits) >= limit:
                break
        return hits


def _accept_language(language: str) -> str:
    if language.startswith("zh"):
        return "zh-CN,zh;q=0.9,en;q=0.8"
    if language.startswith("ja"):
        return "ja-JP,ja;q=0.9,en;q=0.8"
    if language.startswith("es"):
        return "es-ES,es;q=0.9,en;q=0.8"
    if language.startswith("ko"):
        return "ko-KR,ko;q=0.9,en;q=0.8"
    return "en-US,en;q=0.9"


def _bing_locale(language: str) -> dict[str, str]:
    if language.startswith("zh"):
        return {"setlang": "zh-hans", "mkt": "zh-CN"}
    if language.startswith("ja"):
        return {"setlang": "ja", "mkt": "ja-JP"}
    if language.startswith("es"):
        return {"setlang": "es", "mkt": "es-ES"}
    if language.startswith("ko"):
        return {"setlang": "ko", "mkt": "ko-KR"}
    return {}


def _decode_bing_url(href: str) -> str:
    """Decode Bing's ``/ck/a?u=a1<base64>`` redirect into the real destination URL."""
    import base64
    import urllib.parse

    if "bing.com/ck/a" not in href:
        return href
    match = re.search(r"[?&]u=([^&]+)", href)
    if not match:
        return href
    encoded = urllib.parse.unquote(match.group(1))
    if encoded.startswith("a1"):
        encoded = encoded[2:]
    encoded = encoded.replace("-", "+").replace("_", "/")
    encoded += "=" * (-len(encoded) % 4)
    try:
        decoded = base64.b64decode(encoded).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - a malformed redirect is not a result
        return href
    return decoded if decoded.startswith("http") else href


class FeedSearchBackend(SearchBackend):
    """Searches real publisher RSS/Atom feeds and returns direct article URLs.

    This is open web research over publisher feeds: the query is arbitrary text and the
    backend ranks feed items by token overlap. It is not a closed DSL.
    """

    name = "publisher_feeds"

    def __init__(self, config: WebSearchConfig, feeds: list[str] | None = None):
        self.config = config
        self.feeds = feeds or DEFAULT_FEEDS

    def search(self, query: str, *, limit: int = 5, language: str = "en") -> list[SearchHit]:
        import httpx

        self.last_error = ""
        tokens = [t.lower() for t in re.split(r"[\s,，。;；:：/、]+", query) if len(t) >= 2]
        if not tokens:
            return []
        scored: list[tuple[float, int, SearchHit]] = []
        for feed in self.feeds:
            try:
                response = safe_request('GET',
                    feed, config=self.config,
                    headers={"User-Agent": self.config.user_agent},
                    timeout=self.config.timeout,
                    follow_redirects=True,
                )
                response.raise_for_status()
                root = ET.fromstring(response.content)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"
                continue
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                description = _strip_tags(item.findtext("description") or "").strip()
                if not link or not title:
                    continue
                haystack = f"{title} {description}".lower()
                matched = [token for token in tokens if token in haystack]
                if not matched:
                    continue
                # Rank by the fraction of query terms matched so a subject-only query
                # (e.g. a player name) outranks a generic topic-only match.
                fraction = len(matched) / len(tokens)
                longest = max(len(token) for token in matched)
                scored.append(
                    (fraction, longest, SearchHit(title=title, url=link, snippet=description[:200], engine=f"feed:{_host(feed)}"))
                )
        scored.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
        seen: set[str] = set()
        results: list[SearchHit] = []
        for _, _, hit in scored:
            if hit.url in seen:
                continue
            seen.add(hit.url)
            results.append(hit)
            if len(results) >= limit:
                break
        return results


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def _host(url: str) -> str:
    return urlparse(url).hostname or ""


# -------------------------------------------------------------------------- fetch


def _read_bounded(response, max_bytes: int) -> bytes:
    """Compatibility API delegating to the sole hardened transport decoder."""
    from app.web.transport import read_bounded
    return read_bounded(response, max_bytes)



def fetch_page(url: str, config: WebSearchConfig) -> FetchedPage:
    import httpx

    started = time.monotonic()
    retrieved_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    current = url
    response = None
    content = b""
    try:
        response = safe_request('GET', url, config=config,
            headers={'User-Agent':config.user_agent, 'Accept-Language':'en-US,en;q=0.9,zh-CN;q=0.8'})
        current = str(response.url)
        content = response.content
    except SSRFError as exc:
        return FetchedPage(url=url, final_url=current, title="", text="", retrieved_at=retrieved_at, ok=False, error=str(exc), error_code="BLOCKED", acquisition=getattr(exc,'acquisition',{}), elapsed_ms=round((time.monotonic()-started)*1000))
    except Exception as exc:  # noqa: BLE001
        return FetchedPage(url=url, final_url=current, title="", text="", retrieved_at=retrieved_at, ok=False, error=f"{type(exc).__name__}: {exc}", error_code="FETCH_ERROR", acquisition=getattr(exc,'acquisition',{}), elapsed_ms=round((time.monotonic()-started)*1000))
    assert response is not None
    final_url = current
    status = response.status_code
    acquisition = response.extensions.get('acquisition',{})
    status_error = {401: "AUTH_REQUIRED", 402: "PAYWALLED", 403: "BLOCKED", 429: "RATE_LIMITED"}.get(status)
    if status_error or status >= 400:
        return FetchedPage(url=url, final_url=final_url, title="", text="", retrieved_at=retrieved_at, ok=False,
                           error=f"HTTP {status}", error_code=status_error or "FETCH_ERROR", status_code=status, acquisition=acquisition,
                           elapsed_ms=round((time.monotonic()-started)*1000))
    content = content[: config.max_bytes]
    title, text = extract_text(content, response.headers.get("content-type", ""))
    canonical, alternates = _representations(content, response.headers.get("content-type", ""), final_url)
    lowered = (title + " " + text[:1000]).lower()
    access_error = (
        "BLOCKED" if any(word in lowered for word in ("captcha", "access denied", "automated access blocked")) else
        "AUTH_REQUIRED" if any(word in lowered for word in ("sign in to continue", "log in to continue")) else
        "PAYWALLED" if any(word in lowered for word in ("subscribe to read", "subscriber-only", "subscription required")) else ""
    )
    if access_error:
        return FetchedPage(url=url, final_url=final_url, title=title, text="", retrieved_at=retrieved_at,
                           ok=False, error=access_error.lower(), error_code=access_error, content_hash=_content_hash(content),
                           canonical_url=canonical, alternate_urls=alternates, status_code=status, acquisition=acquisition,
                           raw_content=content, content_type=response.headers.get("content-type", ""),
                           elapsed_ms=round((time.monotonic()-started)*1000))
    if len(text) < MIN_PAGE_TEXT:
        js = b"__NEXT_DATA__" in content or b"id=\"app\"" in content or b"id=\"root\"" in content
        return FetchedPage(url=url, final_url=final_url, title=title, text=text, retrieved_at=retrieved_at, ok=False,
                           error="page body too short to ground a claim", error_code="JS_RENDER_REQUIRED" if js else "NO_BODY",
                           content_hash=_content_hash(content), canonical_url=canonical, alternate_urls=alternates,
                           status_code=status, acquisition=acquisition, raw_content=content, content_type=response.headers.get("content-type", ""),
                           elapsed_ms=round((time.monotonic()-started)*1000))
    return FetchedPage(
        url=url,
        final_url=final_url,
        title=title,
        text=text,
        retrieved_at=retrieved_at,
        ok=True,
        content_hash=_content_hash(content),
        publication_date=_publication_date(content, text),
        canonical_url=canonical,
        alternate_urls=alternates,
        status_code=status,
        raw_content=content,
        acquisition=acquisition,
        content_type=response.headers.get("content-type", ""),
        elapsed_ms=round((time.monotonic()-started)*1000),
    )


def _representations(content: bytes, content_type: str, base_url: str) -> tuple[str, tuple[str, ...]]:
    if "html" not in content_type.lower():
        return "", ()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(content[:100000], "html.parser")
    canonical = ""
    alternates: list[str] = []
    for link in soup.find_all("link", href=True):
        rel = {str(item).lower() for item in (link.get("rel") or [])}
        href = urljoin(base_url, str(link["href"]))
        if urlparse(href).scheme not in {"http", "https"}:
            continue
        if "canonical" in rel and not canonical:
            canonical = href
        elif "alternate" in rel and (
            "html" in str(link.get("type") or "") or "rss" in str(link.get("type") or "")
            or "atom" in str(link.get("type") or "") or "amphtml" in rel
        ):
            alternates.append(href)
    return canonical, tuple(dict.fromkeys(alternates[:4]))


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content or b"").hexdigest()


_DATE_PATTERNS = (
    re.compile(r"\b(20\d{2}-\d{2}-\d{2})[T ]\d{2}:\d{2}"),
    re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b"),
    re.compile(r'"datePublished"\s*:\s*"(20\d{2}-\d{2}-\d{2})'),
    re.compile(r'property="article:published_time"\s+content="(20\d{2}-\d{2}-\d{2})'),
)


def _publication_date(content: bytes, text: str) -> str | None:
    head = (content or b"").decode("utf-8", "replace")[:20000]
    for pattern in _DATE_PATTERNS:
        match = pattern.search(head)
        if match:
            return match.group(1)
    match = re.search(r"\b(20\d{2})年(\d{1,2})月(\d{1,2})日", text[:2000])
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return None


_TAG_RE = re.compile(r"<(script|style|nav|footer|header|noscript|svg)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")


def extract_text(content: bytes, content_type: str) -> tuple[str, str]:
    if "html" not in content_type and content_type and "xml" not in content_type:
        try:
            return "", content.decode("utf-8", "replace")[:20000]
        except Exception:  # noqa: BLE001
            return "", ""
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover
        return "", ""
    try:
        soup = BeautifulSoup(content, "html.parser")
    except Exception:  # noqa: BLE001
        return "", ""
    title = (soup.title.string or "").strip() if soup.title else ""
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "form"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = _WS_RE.sub(" ", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return title, "\n".join(lines)[:20000]


def evidence_spans(text: str, query: str, *, max_spans: int = 6) -> list[dict[str, Any]]:
    """Select grounded spans: sentences that share meaningful tokens with the query.

    Each span keeps the passage text *and* its exact offsets in the extracted source
    document. The invariant ``source[start:end] == text`` is enforced so an offset can
    always reproduce the passage (Phase 5B closure, ADR 0022).
    """
    terms = [t for t in re.split(r"[\s,，。;；:：/]+", query) if len(t) >= 2]
    lower_terms = [t.lower() for t in terms]
    spans: list[dict[str, Any]] = []
    for match in re.finditer(r"[^.!?。！？\n]+[.!?。！？]?", text):
        raw = match.group(0)
        sentence = raw.strip()
        if len(sentence) < 25:
            continue
        low = sentence.lower()
        if any(term in low for term in lower_terms):
            start = match.start() + (len(raw) - len(raw.lstrip()))
            # ``end`` must follow the (possibly truncated) stored text exactly.
            stored = text[start : start + 600]
            spans.append(
                {
                    "text": stored,
                    "start": start,
                    "end": start + len(stored),
                    "location": f"offset {start}",
                    "source_type": "DOCUMENT_BODY",
                    "retrieval_status": "OK",
                }
            )
        if len(spans) >= max_spans:
            break
    if not spans and text:
        stored = text[:600]
        spans.append(
            {
                "text": stored,
                "start": 0,
                "end": len(stored),
                "location": "offset 0",
                "source_type": "DOCUMENT_BODY",
                "retrieval_status": "OK",
            }
        )
    return spans
