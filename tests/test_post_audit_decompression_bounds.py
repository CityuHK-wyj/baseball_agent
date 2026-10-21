"""Compression resource bounds at the shared transport, not a retained slice.

Oversized input now fails closed instead of accepting truncated compressed bodies.
Instrumentation wraps the actual production zlib decoder and counts every produced
byte, including the one-byte overflow sentinel. No unbounded flush is allowed.
"""
import gzip
import zlib
import tracemalloc
import httpx
import pytest
from app.web import transport
from app.capabilities.web_search import _read_bounded


class _Stream(httpx.SyncByteStream):
    def __init__(self,chunks):
        self.chunks,self.received,self.closed=chunks,0,False
    def __iter__(self):
        for chunk in self.chunks:
            self.received+=len(chunk)
            yield chunk
    def close(self): self.closed=True


@pytest.fixture
def counter(monkeypatch):
    counts={'produced':0,'largest_limit':0,'unbounded':0}
    real=zlib.decompressobj
    class Decoder:
        def __init__(self,*a,**kw): self.obj=real(*a,**kw)
        def decompress(self,data,max_length=0):
            counts['largest_limit']=max(counts['largest_limit'],max_length)
            counts['unbounded']+=not bool(max_length)
            out=self.obj.decompress(data,max_length)
            counts['produced']+=len(out)
            return out
        def __getattr__(self,name):
            if name=='flush': pytest.fail('unbounded decoder flush')
            return getattr(self.obj,name)
    monkeypatch.setattr(transport.zlib,'decompressobj',Decoder)
    return counts


def bounded(raw,encoding,cap,chunk_size=256,headers=None):
    stream=_Stream([raw[i:i+chunk_size] for i in range(0,len(raw),chunk_size)])
    response=httpx.Response(200,headers={'content-encoding':encoding,**(headers or {})},stream=stream)
    try:
        body=_read_bounded(response,cap)
    except transport.BodyLimitError:
        body=None
    finally:
        response.close()
    assert stream.closed
    return body,stream


@pytest.mark.parametrize('encoding',['gzip','deflate'])
def test_decoded_production_is_bounded_for_compression_bombs(counter,encoding):
    raw=gzip.compress(b'Z'*4_000_000) if encoding=='gzip' else zlib.compress(b'Z'*4_000_000)[2:-4]
    cap=12289
    tracemalloc.start()
    try:
        body,stream=bounded(raw,encoding,cap)
        _,peak=tracemalloc.get_traced_memory()
    finally: tracemalloc.stop()
    assert body is None
    assert 0 < counter['produced'] <= cap+1
    assert counter['largest_limit'] <= cap+1 and not counter['unbounded']
    assert peak<2_000_000


def test_retained_body_bound_holds_for_large_compression_ratio(counter):
    body,_=bounded(gzip.compress(b'Z'*8_000_000),'gzip',512)
    assert body is None and counter['produced']<=513


def test_chunked_compressed_body_is_bounded(counter):
    body,_=bounded(gzip.compress(b'Z'*4_000_000),'gzip',12289,7)
    assert body is None and counter['produced']<=12290


def test_no_content_length_compressed_body_is_bounded(counter):
    body,_=bounded(gzip.compress(b'Z'*4_000_000),'gzip',100)
    assert body is None and counter['produced']<=101


def test_content_length_compressed_body_is_bounded(counter):
    raw=gzip.compress(b'Z'*4_000_000)
    body,_=bounded(raw,'gzip',256,headers={'Content-Length':str(len(raw))})
    assert body is None and counter['produced']<=257


def test_normal_compressed_page_is_fully_decoded():
    text=b'A normal public article body that grounds a factual claim. '*40
    body,_=bounded(gzip.compress(text),'gzip',2_000_000)
    assert body==text


def test_identity_body_still_bounded():
    body,stream=bounded(b'x'*5000,'identity',100,128)
    assert body is None and stream.received<=228


def test_unknown_content_encoding_fails_closed():
    with pytest.raises(ValueError): bounded(b'unknown','br',1024)
