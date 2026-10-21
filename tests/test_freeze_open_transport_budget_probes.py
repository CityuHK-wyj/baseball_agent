"""Owned, bounded reproduction of remaining transport/accounting audit defects.

Strict XFAIL records OPEN findings, never safety passes. No uncontrolled public or
private target is contacted: DNS is isolated to one owned loopback fixture; body
probes use an in-memory HTTPX transport and bounded fixture sizes.
"""
import gzip
from http.server import BaseHTTPRequestHandler, HTTPServer
import socket
import threading
from types import SimpleNamespace

import httpx
import pytest

from app.capabilities.web_search import WebSearchConfig, fetch_page
from app.capabilities.web_research import WebResearchTool, _LLM_VERIFY_BUDGET
from tests.test_web_evidence_semantics_wsa01_02 import _document, _injury_current, _span


def test_rebinding_never_reaches_owned_private_fixture(monkeypatch):
    reached = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            reached.append(self.path)
            body = ("Owned fixture body. " * 30).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    calls = []

    def resolve(host, port, *args, **kwargs):
        # Fail closed on any accidental unrelated resolution; all acquisition must
        # stay within this owned fixture. Ephemeral service port keeps the test isolated.
        assert host in {"owned-rebinding.example.test", b"owned-rebinding.example.test"}
        calls.append(host)
        address = "93.184.216.34" if len(calls) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                 (address, server.server_port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    for name in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    try:
        page = fetch_page("http://owned-rebinding.example.test/fixture", WebSearchConfig(timeout=2))
        # The connection is pinned to the first validated address; no second resolver
        # call can rebind the host to the private fixture.
        assert not reached, "private fixture was acquired after a public address check"
        assert not page.ok
        assert len(calls) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class FixtureBody(httpx.SyncByteStream):
    def __init__(self, body):
        self.body = body
        self.received = 0
        self.closed = False

    def __iter__(self):
        for index in range(0, len(self.body), 256):
            chunk = self.body[index:index + 256]
            self.received += len(chunk)
            yield chunk

    def close(self):
        self.closed = True


@pytest.mark.parametrize("mode", ["content_length", "chunked", "compressed"])
def test_acquisition_cap_applies_before_unbounded_buffering(monkeypatch, mode):
    body = b"Owned oversized response. " * 1000
    raw = gzip.compress(body) if mode == "compressed" else body
    stream = FixtureBody(raw)
    headers = {"Content-Type": "text/plain"}
    if mode == "content_length":
        headers["Content-Length"] = str(len(raw))
    elif mode == "compressed":
        headers["Content-Encoding"] = "gzip"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, stream=stream))
    real_client = httpx.Client

    def fixture_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", fixture_client)
    monkeypatch.setattr(
        "app.capabilities.web_search._pinned_target",
        lambda url: ("https://93.184.216.34/", "owned-body.example.test",
                     {"Host": "owned-body.example.test"}, {"sni_hostname": "owned-body.example.test"}),
    )
    page = fetch_page("https://owned-body.example.test/", WebSearchConfig(max_bytes=1024))
    # The acquisition cap is applied while streaming; the oversized/gzip body is never
    # fully buffered, and the underlying stream is closed.
    assert len(page.raw_content) <= 1024
    assert stream.closed


def test_failed_assessor_attempts_consume_budget():
    class UnavailableAssessor:
        configured = True
        calls = 0

        def complete_json(self, **kwargs):
            self.calls += 1
            raise RuntimeError("owned assessor outage")

    llm = UnavailableAssessor()
    tool = WebResearchTool(SimpleNamespace(feeds=[], broad_search=False), llm=llm)
    prop = _injury_current()
    findings, documents = [], []
    for i in range(_LLM_VERIFY_BUDGET + 3):
        text = f"Shohei Ohtani injury status: roster update number {i} remains unconfirmed."
        doc = _document(text, "2026-10-01", url=f"https://owned.example.test/{i}")
        doc.id = f"doc-{i}"
        span = _span(text, doc)
        findings.append({"document_id": doc.id, "evidence_spans": [span.model_dump(mode="json")]})
        documents.append(doc.model_dump(mode="json"))
    tool._verify([prop], findings, documents)
    assert llm.calls > 0
    assert llm.calls <= _LLM_VERIFY_BUDGET and tool._llm_verify_calls == llm.calls, (
        f"attempts={llm.calls}, cap={_LLM_VERIFY_BUDGET}, reported={tool._llm_verify_calls}")
