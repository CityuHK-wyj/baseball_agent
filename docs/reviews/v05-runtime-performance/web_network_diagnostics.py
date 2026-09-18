"""Layered web/network diagnostics for the v0.5 audit.

Diagnoses the Web path independently of the Planner and without weakening any security
boundary (the SSRF guard is *not* disabled or bypassed). It measures each network layer
separately so the first failing boundary can be identified:

    DNS -> address classification -> proxy environment -> TCP -> TLS/HTTP ->
    search backend -> hit parsing -> page fetch -> redirect -> bounded body read ->
    SSRF policy -> evidence extraction

Writes ``web_diagnostics.json``. Credentials embedded in proxy URLs are redacted.

Usage:
    python3 docs/reviews/v05-runtime-performance/web_network_diagnostics.py
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

AUDIT = Path(__file__).resolve().parent
REPO = AUDIT.parents[2]
sys.path.insert(0, str(REPO))

SEARCH_HOSTS = ["lite.duckduckgo.com", "html.duckduckgo.com", "www.bing.com",
                "duckduckgo.com", "example.com", "statsapi.mlb.com", "api.deepseek.com"]

PROXY_VARS = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
              "http_proxy", "https_proxy", "all_proxy", "no_proxy"]


def _redact_proxy(value: str) -> str:
    from app.tools.results import redact_secrets

    redacted = redact_secrets(value, ())
    if "://" in redacted and "@" in redacted:
        scheme, _, rest = redacted.partition("://")
        _creds, _, host = rest.rpartition("@")
        return f"{scheme}://<redacted>@{host}"
    return redacted


def proxy_environment() -> dict:
    result = {}
    for name in PROXY_VARS:
        value = os.getenv(name)
        result[name] = {"configured": bool(value),
                        "value": _redact_proxy(value) if value else ""}
    return result


def dns_inventory() -> dict:
    result = {}
    for host in SEARCH_HOSTS:
        entry = {"host": host}
        try:
            infos = socket.getaddrinfo(host, None)
            addresses = sorted({info[4][0] for info in infos})
            classified = []
            for address in addresses:
                try:
                    ip = ipaddress.ip_address(address)
                    classified.append({
                        "address": address, "version": ip.version,
                        "is_private": ip.is_private, "is_loopback": ip.is_loopback,
                        "is_link_local": ip.is_link_local, "is_reserved": ip.is_reserved,
                        "is_multicast": ip.is_multicast, "is_global": ip.is_global,
                        "is_unspecified": ip.is_unspecified,
                        "in_benchmark_range": address.startswith("198.18.")
                        or address.startswith("198.19."),
                    })
                except ValueError:
                    classified.append({"address": address, "parse_error": True})
            entry["addresses"] = classified
            entry["resolved"] = True
        except OSError as error:
            entry["resolved"] = False
            entry["error"] = type(error).__name__
        result[host] = entry
    return result


def tcp_probe(host: str, port: int = 443, timeout: float = 8.0) -> dict:
    start = time.perf_counter()
    try:
        connection = socket.create_connection((host, port), timeout=timeout)
        peer = connection.getpeername()
        connection.close()
        return {"ok": True, "peer": list(peer),
                "duration_ms": round((time.perf_counter() - start) * 1000, 2)}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": f"{type(error).__name__}: {error}",
                "duration_ms": round((time.perf_counter() - start) * 1000, 2)}


def http_probe(url: str, timeout: float = 12.0) -> dict:
    """A *raw* HTTP probe that deliberately does not apply the app SSRF guard.

    This separates 'the environment/network can reach the host' from 'the application
    policy refuses the host'. It performs a bounded GET and reports status/headers only.
    """
    import requests

    start = time.perf_counter()
    try:
        response = requests.get(url, timeout=timeout, allow_redirects=False,
                                headers={"User-Agent": "baseball-agent-audit/0.5"})
        return {"ok": True, "status": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "location": response.headers.get("Location", ""),
                "bytes": len(response.content),
                "duration_ms": round((time.perf_counter() - start) * 1000, 2)}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": f"{type(error).__name__}: {str(error)[:200]}",
                "duration_ms": round((time.perf_counter() - start) * 1000, 2)}


def resolver_comparison() -> dict:
    """Compare resolver paths: shell getent, Python socket, requests, urllib."""
    import subprocess

    shell = {}
    for host in ("lite.duckduckgo.com", "www.bing.com", "statsapi.mlb.com"):
        try:
            out = subprocess.run(["getent", "ahosts", host], capture_output=True,
                                 text=True, timeout=10)
            shell[host] = sorted({line.split()[0] for line in out.stdout.splitlines()
                                  if line.split()})
        except Exception as error:  # noqa: BLE001
            shell[host] = f"error: {type(error).__name__}"
    return {"shell_getent": shell, "python_socket": {
        host: sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
        for host in shell}}


def ssrf_guard_probe() -> dict:
    """What the application's SSRF guard decides — never bypassed."""
    from app.tools.web_research import _host_is_safe

    urls = [
        "https://lite.duckduckgo.com/lite/?q=test",
        "https://html.duckduckgo.com/html/?q=test",
        "https://www.bing.com/search?q=test",
        "https://statsapi.mlb.com/api/v1/teams",
        "https://example.com/",
        "http://127.0.0.1/",
        "http://localhost/",
        "http://10.0.0.1/",
    ]
    result = {}
    for url in urls:
        host = urlparse(url).hostname or ""
        try:
            addresses = sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
        except OSError:
            addresses = []
        result[url] = {"host": host, "resolved": addresses, "host_is_safe": _host_is_safe(url)}
    return result


def live_web_path() -> dict:
    """Exercise the real WebResearchTool search + PageReader paths."""
    from app.tools.web_research import (PageReader, WebResearchTool)

    tool = WebResearchTool()
    search_start = time.perf_counter()
    search = {}
    try:
        results = tool.search("MLB test query")
        search = {"ok": True, "hits": len(results),
                  "first_url": results[0].url if results else "",
                  "snippet_present": bool(results[0].snippet) if results else False,
                  "duration_ms": round((time.perf_counter() - search_start) * 1000, 2)}
    except Exception as error:  # noqa: BLE001
        search = {"ok": False, "error": f"{type(error).__name__}: {str(error)[:200]}",
                  "duration_ms": round((time.perf_counter() - search_start) * 1000, 2)}

    reader_start = time.perf_counter()
    try:
        text = PageReader().read("https://example.com/")
        page = {"ok": bool(text), "chars": len(text),
                "duration_ms": round((time.perf_counter() - reader_start) * 1000, 2)}
    except Exception as error:  # noqa: BLE001
        page = {"ok": False, "error": f"{type(error).__name__}: {str(error)[:200]}",
                "duration_ms": round((time.perf_counter() - reader_start) * 1000, 2)}

    research_start = time.perf_counter()
    try:
        items = tool.research("MLB test query", fetch_pages=2)
        research = {"ok": True, "items": len(items),
                    "grounded": sum(1 for item in items
                                    if (item.data or {}).get("fetched")),
                    "duration_ms": round((time.perf_counter() - research_start) * 1000, 2)}
    except Exception as error:  # noqa: BLE001
        research = {"ok": False, "error": f"{type(error).__name__}: {str(error)[:200]}",
                    "duration_ms": round((time.perf_counter() - research_start) * 1000, 2)}
    return {"search": search, "page_read": page, "research": research}


def classify(proxies: dict, dns: dict, http: dict, guard: dict, live: dict) -> dict:
    """Determine the first failing boundary from the measured evidence."""
    findings: list[str] = []
    first_failing = "NONE"

    using_proxy = any(entry["configured"] for name, entry in proxies.items()
                      if name.lower() in ("http_proxy", "https_proxy", "all_proxy"))
    benchmark_hosts = [
        host for host, entry in dns.items()
        if entry.get("resolved") and all(
            (a.get("in_benchmark_range") and a.get("is_private"))
            for a in entry.get("addresses", []))
    ]
    if benchmark_hosts:
        findings.append(
            "DNS resolves public hostnames into the 198.18.0.0/15 benchmarking range "
            "(a fake-IP transparent-proxy pattern); ipaddress classifies these as "
            "private, so the SSRF guard refuses them.")
    if not using_proxy:
        findings.append("No HTTP(S)_PROXY / ALL_PROXY environment variable is set; the "
                        "interception is transparent (resolver-level), not SDK-level.")

    network_reachable = any(entry.get("ok") for entry in http.values())
    guard_blocks_public = [url for url, entry in guard.items()
                           if entry["host"] not in ("127.0.0.1", "localhost", "10.0.0.1")
                           and not entry["host_is_safe"]]
    if network_reachable and guard_blocks_public:
        first_failing = "SSRF_POLICY_BLOCK_ON_RESERVED_RESOLUTION"

    if not live["search"].get("ok"):
        findings.append(f"Web search fails: {live['search'].get('error', '')}")
    if not live["page_read"].get("ok"):
        findings.append("PageReader returns no text (guard refuses the initial URL).")
    if not live["research"].get("ok"):
        findings.append(f"WebResearchTool.research fails: {live['research'].get('error', '')}")

    # Distinguish 'search works' from 'search is blocked before the network'.
    search_hosts = [url for url, entry in guard.items()
                    if entry["host"].endswith("duckduckgo.com")
                    or entry["host"].endswith("bing.com")]
    search_hosts_blocked = bool(search_hosts) and all(
        not guard[url]["host_is_safe"] for url in search_hosts)
    search_blocked_pre_network = (not live["search"].get("ok")) and search_hosts_blocked
    return {
        "first_failing_boundary": first_failing,
        "transparent_proxy_fake_ip": bool(benchmark_hosts),
        "proxy_env_configured": using_proxy,
        "network_reachable_without_guard": network_reachable,
        "guard_blocks_public_hosts": guard_blocks_public,
        "search_blocked_before_network": search_blocked_pre_network,
        "findings": findings,
    }


def main() -> int:
    proxies = proxy_environment()
    dns = dns_inventory()
    resolvers = resolver_comparison()
    tcp = {host: tcp_probe(host) for host in SEARCH_HOSTS if host != "api.deepseek.com"}
    http = {
        "https://example.com/": http_probe("https://example.com/"),
        "https://lite.duckduckgo.com/lite/?q=test": http_probe(
            "https://lite.duckduckgo.com/lite/?q=test"),
        "https://www.bing.com/search?q=test": http_probe("https://www.bing.com/search?q=test"),
        "https://statsapi.mlb.com/api/v1/teams?sportId=1": http_probe(
            "https://statsapi.mlb.com/api/v1/teams?sportId=1"),
    }
    guard = ssrf_guard_probe()
    live = live_web_path()
    conclusion = classify(proxies, dns, http, guard, live)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "proxy_environment": proxies,
        "resolv_conf": _read("/etc/resolv.conf"),
        "nsswitch_hosts": _grep("/etc/nsswitch.conf", "hosts"),
        "dns_resolution": dns,
        "resolver_comparison": resolvers,
        "tcp_probe": tcp,
        "http_probe_without_ssrf_guard": http,
        "ssrf_guard_decisions": guard,
        "live_web_path": live,
        "conclusion": conclusion,
    }
    out = AUDIT / "web_diagnostics.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(conclusion, indent=2, ensure_ascii=False))
    print(f"wrote {out}")
    return 0


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception as error:  # noqa: BLE001
        return f"<unreadable: {type(error).__name__}>"


def _grep(path: str, needle: str) -> str:
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(needle):
                return line
    except Exception:  # noqa: BLE001
        pass
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
