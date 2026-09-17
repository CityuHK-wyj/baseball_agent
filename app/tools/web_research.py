"""Live, first-class web research.

This is a normal Planner tool, not an emergency fallback. It performs a real search
against a keyless public endpoint (DuckDuckGo Lite) with a Bing fallback, and can fetch
and read result pages. Output is *unstructured evidence with provenance*; it is never
forced into the SQL schema and is never auto-promoted to authoritative Shared Knowledge.

Safety: HTTP(S) only, bounded results/bytes/time, and a basic SSRF guard that refuses
loopback/private hosts. It does not scrape arbitrary sites beyond normal page fetch and
does not bypass access controls.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import ipaddress
import re
import socket
from urllib.parse import parse_qs, quote, unquote, urlparse

from app.models.agent_runtime import EvidenceItem

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/120.0.0.0 Safari/537.36")
_MAX_BYTES = 400_000
_MAX_TEXT = 6000


class WebResearchUnavailable(RuntimeError):
    """The search backend could not be reached. Safe to surface as a recovery signal."""


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = "web"


@dataclass
class _Transport:
    """Bounded HTTP transport with an explicit, SSRF-checked redirect policy.

    Redirects are followed manually so every destination is validated, and the body is
    streamed with a byte cap (never an unbounded download sliced afterwards).
    """

    timeout: float = 15.0
    user_agent: str = _UA
    max_redirects: int = 4

    def get(self, url: str) -> tuple[int, str, str]:
        import requests
        from urllib.parse import urljoin

        current = url
        for _ in range(self.max_redirects + 1):
            if not _host_is_safe(current):
                raise WebResearchUnavailable(f"refusing unsafe host for {current!r}")
            response = requests.get(
                current, headers={"User-Agent": self.user_agent,
                                  "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
                timeout=self.timeout, allow_redirects=False, stream=True)
            location = response.headers.get("Location")
            if response.status_code in (301, 302, 303, 307, 308) and location:
                response.close()
                current = urljoin(current, location)
                continue
            chunks: list[bytes] = []
            total = 0
            try:
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= _MAX_BYTES:
                        break
            finally:
                response.close()
            raw = b"".join(chunks)[:_MAX_BYTES]
            try:
                text = raw.decode(response.encoding or "utf-8", errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            # Return the final source identity so callers record where bytes came from.
            return response.status_code, text, current
        raise WebResearchUnavailable("too many redirects")


def _decode_ddg_url(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return unquote(target[0])
    return href


class DuckDuckGoLiteSearch:
    """Keyless public search. Raises ``WebResearchUnavailable`` on hard failure."""

    name = "duckduckgo-lite"

    def __init__(self, transport: _Transport | None = None, max_results: int = 6) -> None:
        self._transport = transport or _Transport()
        self._max_results = max_results

    def search(self, query: str) -> tuple[WebSearchResult, ...]:
        from bs4 import BeautifulSoup
        urls = (
            f"https://lite.duckduckgo.com/lite/?q={quote(query)}&kp=1",
            f"https://html.duckduckgo.com/html/?q={quote(query)}&kp=1",
        )
        last_error: Exception | None = None
        for url in urls:
            try:
                status, html, _ = self._transport.get(url)
            except Exception as error:  # noqa: BLE001 - network failures are recoverable
                last_error = error
                continue
            if status != 200 or not html:
                continue
            results = self._parse(html)
            if results:
                return results[:self._max_results]
        raise WebResearchUnavailable(
            f"web search failed: {type(last_error).__name__ if last_error else 'no results'}")

    @staticmethod
    def _parse(html: str) -> tuple[WebSearchResult, ...]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        results: list[WebSearchResult] = []
        links = soup.select("a.result-link")
        snippets = soup.select(".result-snippet")
        if links:
            for index, anchor in enumerate(links):
                href = _decode_ddg_url(anchor.get("href", ""))
                if not href.startswith("http"):
                    continue
                snippet = snippets[index].get_text(" ", strip=True) if index < len(snippets) else ""
                results.append(WebSearchResult(title=anchor.get_text(" ", strip=True),
                                               url=href, snippet=snippet))
            return tuple(results)
        # Fallback for the html endpoint markup.
        for block in soup.select(".result"):
            anchor = block.select_one(".result__a")
            if anchor is None:
                continue
            href = _decode_ddg_url(anchor.get("href", ""))
            snippet_node = block.select_one(".result__snippet")
            results.append(WebSearchResult(
                title=anchor.get_text(" ", strip=True), url=href,
                snippet=snippet_node.get_text(" ", strip=True) if snippet_node else ""))
        return tuple(results)


class BingSearch:
    """Fallback keyless search used when DuckDuckGo returns a challenge page."""

    name = "bing"

    def __init__(self, transport: _Transport | None = None, max_results: int = 6) -> None:
        self._transport = transport or _Transport()
        self._max_results = max_results

    def search(self, query: str) -> tuple[WebSearchResult, ...]:
        from bs4 import BeautifulSoup
        status, html, _ = self._transport.get(f"https://www.bing.com/search?q={quote(query)}")
        if status != 200 or not html:
            raise WebResearchUnavailable("bing search failed")
        soup = BeautifulSoup(html, "html.parser")
        results: list[WebSearchResult] = []
        for block in soup.select("li.b_algo"):
            anchor = block.select_one("h2 a")
            if anchor is None or not anchor.get("href", "").startswith("http"):
                continue
            snippet_node = block.select_one(".b_caption p") or block.select_one("p")
            results.append(WebSearchResult(
                title=anchor.get_text(" ", strip=True), url=anchor.get("href"),
                snippet=snippet_node.get_text(" ", strip=True) if snippet_node else ""))
        if not results:
            raise WebResearchUnavailable("bing returned no parseable results")
        return tuple(results[:self._max_results])


def _host_is_safe(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname
    if host in ("localhost", "metadata.google.internal"):
        return False
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return True


class PageReader:
    """Fetch and extract readable text from one result page."""

    def __init__(self, transport: _Transport | None = None) -> None:
        self._transport = transport or _Transport()

    def read(self, url: str) -> str:
        if not _host_is_safe(url):
            return ""
        try:
            status, html, final_url = self._transport.get(url)
        except Exception:  # noqa: BLE001 - a page fetch failure is not fatal
            return ""
        # The final destination must satisfy the same policy as the initial URL.
        if not _host_is_safe(final_url):
            return ""
        if status != 200 or not html:
            return ""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "form"]):
            tag.decompose()
        text = re.sub(r"\n{2,}", "\n", soup.get_text("\n", strip=True))
        return text[:_MAX_TEXT]


class WebResearchTool:
    """Planner-facing tool: free-form query in, evidenced web findings out."""

    name = "web-research"

    def __init__(self, backends: tuple[object, ...] | None = None,
                 reader: PageReader | None = None, fetch_pages: int = 2,
                 max_results: int = 6) -> None:
        self._backends = backends or (DuckDuckGoLiteSearch(max_results=max_results),
                                      BingSearch(max_results=max_results))
        self._reader = reader or PageReader()
        self._fetch_pages = fetch_pages
        self._max_results = max_results

    def search(self, query: str) -> tuple[WebSearchResult, ...]:
        last: Exception | None = None
        for backend in self._backends:
            try:
                results = backend.search(query)  # type: ignore[attr-defined]
            except Exception as error:  # noqa: BLE001 - try the next backend
                last = error
                continue
            if results:
                return results
        raise WebResearchUnavailable(
            f"all web search backends failed: {type(last).__name__ if last else 'unknown'}")

    def research(self, query: str, *, fetch_pages: int | None = None) -> tuple[EvidenceItem, ...]:
        results = self.search(query)
        pages = self._fetch_pages if fetch_pages is None else fetch_pages
        evidence: list[EvidenceItem] = []
        fetched = 0
        for result in results:
            text = ""
            fetched_page = False
            if fetched < pages:
                text = self._reader.read(result.url)
                if text:
                    fetched += 1
                    fetched_page = True
            evidence.append(EvidenceItem(
                kind="WEB", summary=result.title or query, source=urlparse(result.url).netloc,
                reference=result.url,
                text=(text or result.snippet)[:_MAX_TEXT],
                data={"query": query, "snippet": result.snippet, "title": result.title,
                      "url": result.url, "fetched": fetched_page,
                      "retrieved_at": datetime.now(timezone.utc).isoformat()},
                # A search snippet is a hit, not grounded evidence. Only a retrieved
                # page body is accepted as support.
                accepted=fetched_page,
                retrieved_at=datetime.now(timezone.utc)))
        return tuple(evidence)
