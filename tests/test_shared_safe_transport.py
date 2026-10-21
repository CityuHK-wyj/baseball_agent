"""Shared acquisition edge cases with owned streams, DNS and private listeners."""
import gzip
import socket
import threading
import tracemalloc
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx
import pytest
from app.web.transport import request, read_bounded, TransportTelemetry, BodyLimitError
from app.capabilities.web_search import WebSearchConfig, SSRFError
from app.web.fetch_ladder import _feed_item, fetch_with_fallback


class Chunks(httpx.SyncByteStream):
    def __init__(self,data,size=13):
        self.data,self.size,self.delivered,self.closed = data,size,0,False
    def __iter__(self):
        yield b''
        for i in range(0,len(self.data),self.size):
            chunk = self.data[i:i+self.size]
            self.delivered += len(chunk)
            yield chunk
    def close(self):
        self.closed = True


def install(monkeypatch,handler):
    original = httpx.Client
    def factory(*args,**kwargs):
        kwargs['transport'] = httpx.MockTransport(handler)
        return original(*args,**kwargs)
    monkeypatch.setattr(httpx,'Client',factory)
    monkeypatch.setattr(socket,'getaddrinfo',lambda host,port,*a,**k:[
        (socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',port))])


@pytest.mark.parametrize('encoding',['identity','gzip','deflate','raw-deflate','concatenated'])
@pytest.mark.parametrize('chunk_size',[1,2,13,8192,65536])
def test_decoding_exact_and_streams_closed(monkeypatch,encoding,chunk_size):
    body = b'Bounded factual source. '*100
    if encoding == 'gzip': raw = gzip.compress(body)
    elif encoding == 'concatenated': raw = gzip.compress(body[:23])+gzip.compress(body[23:])
    elif encoding == 'deflate': raw = zlib.compress(body)
    elif encoding == 'raw-deflate': raw = zlib.compress(body)[2:-4]
    else: raw = body
    stream = Chunks(raw,chunk_size)
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(200,headers={'content-encoding':'deflate' if encoding=='raw-deflate' else
            'gzip' if encoding=='concatenated' else encoding},stream=stream)
    install(monkeypatch,handler)
    response = request('GET','https://owned.example/source',max_bytes=10000)
    assert response.content == body
    assert stream.closed
    assert requests[0].url.host == '93.184.216.34'
    assert requests[0].headers['host'] == 'owned.example'
    assert requests[0].extensions['sni_hostname'] == 'owned.example'
    assert response.extensions['acquisition']['raw_bytes'] == len(raw)
    assert response.extensions['acquisition']['decoded_bytes'] == len(body)


@pytest.mark.parametrize('encoding,raw',[
    ('br',b'unknown'), ('gzip',b'malformed'), ('gzip',gzip.compress(b'hello')[:-3]),
    ('deflate',zlib.compress(b'hello')[:-1]), ('gzip',gzip.compress(b'hello')+b'junk'),
])
def test_malformed_truncated_unknown_encoding_fails_closed(monkeypatch,encoding,raw):
    stream = Chunks(raw,1)
    install(monkeypatch,lambda req:httpx.Response(200,headers={'content-encoding':encoding},stream=stream))
    with pytest.raises((ValueError,zlib.error)):
        request('GET','https://owned.example/',max_bytes=1000)
    assert stream.closed


@pytest.mark.parametrize('mode',['identity','gzip','deflate'])
def test_resource_caps_apply_to_production_not_only_retained_slice(monkeypatch,mode):
    body = b'x'*4_000_000
    raw = gzip.compress(body) if mode == 'gzip' else zlib.compress(body) if mode == 'deflate' else body
    stream = Chunks(raw,4096)
    install(monkeypatch,lambda req:httpx.Response(200,headers={'content-encoding':mode},stream=stream))
    tracemalloc.start()
    try:
        with pytest.raises(BodyLimitError):
            request('GET','https://owned.example/',max_bytes=12289)
        _,peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2_000_000, peak
    assert stream.closed
    assert stream.delivered <= 12289+8192


def test_default_rss_alternate_bomb_never_uses_httpx_auto_decoder(monkeypatch):
    from httpx._decoders import GZipDecoder
    compressed = gzip.compress(b'<rss>'+b'x'*4_000_000+b'</rss>')
    stream = Chunks(compressed)
    def handler(req):
        if req.url.path == '/article':
            body = b'<html><link rel="alternate" type="application/rss+xml" href="/feed.xml"><body>Short.</body></html>'
            return httpx.Response(200,headers={'content-type':'text/html'},stream=Chunks(body))
        return httpx.Response(200,headers={'content-encoding':'gzip'},stream=stream)
    install(monkeypatch,handler)
    def forbidden(*args,**kwargs):
        pytest.fail('unbounded HTTPX automatic decoder invoked')
    monkeypatch.setattr(GZipDecoder,'decode',forbidden)
    page = fetch_with_fallback('https://owned.example/article',WebSearchConfig(max_bytes=12289))
    assert not page.ok
    assert page.fallback_steps[-1] == 'rss_item:FETCH_ERROR'
    assert stream.closed
    assert len(page.raw_content) <= 12289
    assert page.acquisition['decoded_bytes'] <= 12290
    assert page.acquisition['limit_exceeded'] is True


@pytest.fixture
def private_listener():
    reached=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            reached.append(self.path)
            self.send_response(200);self.end_headers();self.wfile.write(b'private')
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield server,reached
    server.shutdown();server.server_close();thread.join(timeout=2)
    assert reached == [], 'private fixture must receive zero requests'


@pytest.mark.parametrize('address',['127.0.0.1','10.0.0.1','169.254.169.254','::1','fc00::1','fe80::1','::ffff:127.0.0.1'])
def test_private_resolutions_receive_zero_private_requests(monkeypatch,private_listener,address):
    server,reached=private_listener
    monkeypatch.setattr(socket,'getaddrinfo',lambda host,port,*a,**k:[
        (socket.AF_INET6 if ':' in address else socket.AF_INET,socket.SOCK_STREAM,6,'',(address,server.server_port))])
    with pytest.raises(SSRFError):
        request('GET','https://owned.example/')
    assert not reached


def test_rss_check_connect_rebinding_is_pinned(monkeypatch,private_listener):
    server,reached=private_listener
    calls=[]
    def resolve(host,port,*a,**kwargs):
        if host in {'owned.example',b'owned.example'}:
            calls.append(host)
            address='93.184.216.34' if len(calls)==1 else '127.0.0.1'
            return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,server.server_port))]
        # Do not connect to an uncontrolled real public service in this owned probe.
        raise socket.gaierror('owned public endpoint unavailable')
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    page=_feed_item('http://owned.example/feed.xml','http://owned.example/article',WebSearchConfig(timeout=.2))
    assert not page.ok and not reached
    assert len(calls)==1


def test_redirect_revalidates_destination_and_never_dials_private(monkeypatch,private_listener):
    server,reached=private_listener
    calls=[]
    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(302,headers={'location':'http://127.0.0.1/private'},stream=Chunks(b''))
    install(monkeypatch,handler)
    with pytest.raises(SSRFError):
        request('GET','http://owned.example/')
    assert len(calls)==1 and not reached


def test_active_browser_cannot_escape_safe_transport():
    from app.web.fetch_ladder import render_public_page
    page=render_public_page('https://owned.example/',WebSearchConfig())
    assert not page.ok and page.error_code=='JS_RENDER_REQUIRED'
