"""Shared external acquisition contract: pinned destinations and bounded raw decoding.

All redirects are independently resolved and pinned. Host and TLS SNI stay original.
No environment proxy or transparent SDK content decoding participates. Telemetry is
per request and records actual delivered raw bytes, not an inferred decoded length.
"""
from dataclasses import dataclass, asdict
import json as json_module
import zlib
from urllib.parse import urljoin
import httpx


class BodyLimitError(ValueError):
    pass


@dataclass
class TransportTelemetry:
    http_attempts: int = 0
    raw_bytes: int = 0
    decoded_bytes: int = 0
    retained_bytes: int = 0
    redirects: int = 0
    limit_exceeded: bool = False


def read_bounded(response, max_bytes, telemetry=None):
    t = telemetry if telemetry is not None else TransportTelemetry()
    if max_bytes <= 0:
        raise BodyLimitError('positive body bound required')
    encoding = response.headers.get('content-encoding','').strip().lower()
    if encoding in {'identity','none'}:
        encoding = ''
    if encoding not in {'','gzip','x-gzip','deflate'}:
        raise ValueError('unsupported content encoding')
    # Preloaded fixtures have already been decoded by HTTPX. They are never created
    # by request(); do not pretend that their decoded content measures wire bytes.
    if getattr(response,'is_stream_consumed',False):
        body = response.content
        t.decoded_bytes = len(body)
        if len(body) > max_bytes:
            t.limit_exceeded = True
            raise BodyLimitError('preloaded body exceeds retained bound')
        t.retained_bytes = len(body)
        return body
    obj = None
    prefix = b''
    members = 0
    chunks = []
    try:
        # HTTPcore bounds its socket read buffer (64KiB). iter_raw chunks bound the
        # decoder input. num_bytes_downloaded includes transport read-ahead, which
        # may exceed the policy by one fixed socket buffer; it is NOT reported as
        # a capped wire count. No body-sized allocation/read-ahead is used here.
        for raw in response.iter_raw(chunk_size=min(8192,max_bytes)):
            t.raw_bytes = response.num_bytes_downloaded
            if t.raw_bytes > max_bytes:
                t.limit_exceeded = True
                raise BodyLimitError('raw body limit exceeded')
            if not raw:
                continue
            if not encoding:
                decoded = raw
            else:
                if obj is None:
                    prefix += raw
                    if encoding == 'deflate' and len(prefix) < 2:
                        continue
                    if encoding == 'deflate':
                        a,b = prefix[:2]
                        wrapped = a & 15 == 8 and (a*256+b) % 31 == 0
                        obj = zlib.decompressobj(zlib.MAX_WBITS if wrapped else -zlib.MAX_WBITS)
                    else:
                        obj = zlib.decompressobj(16+zlib.MAX_WBITS)
                    raw, prefix = prefix, b''
                pieces = []
                while raw:
                    decoded = obj.decompress(raw,max_bytes-t.decoded_bytes+1)
                    t.decoded_bytes += len(decoded)
                    if t.decoded_bytes > max_bytes or obj.unconsumed_tail:
                        t.limit_exceeded = True
                        raise BodyLimitError('decoded body limit exceeded')
                    pieces.append(decoded)
                    raw = obj.unused_data
                    if raw:
                        if encoding not in {'gzip','x-gzip'} or not obj.eof:
                            raise ValueError('trailing compressed data')
                        members += 1
                        if members >= 16:
                            raise ValueError('too many gzip members')
                        obj = zlib.decompressobj(16+zlib.MAX_WBITS)
                chunks.extend(pieces)
                continue
            t.decoded_bytes += len(decoded)
            if t.decoded_bytes > max_bytes:
                t.limit_exceeded = True
                raise BodyLimitError('decoded body limit exceeded')
            chunks.append(decoded)
        if encoding and (obj is None or not obj.eof):
            raise ValueError('truncated/malformed compressed body')
        body = b''.join(chunks)
        t.retained_bytes = len(body)
        return body
    finally:
        t.raw_bytes = max(t.raw_bytes, response.num_bytes_downloaded)


def request(method, url, *, config=None, timeout=None, max_bytes=None, headers=None,
            params=None, json=None, follow_redirects=True):
    from app.capabilities.web_search import _pinned_target
    limit = max_bytes if max_bytes is not None else getattr(config,'max_bytes',2_000_000)
    timeout = timeout if timeout is not None else getattr(config,'timeout',15.0)
    current = str(httpx.URL(url,params=params)) if params is not None else str(url)
    original_headers = dict(headers or {})
    original_headers.update({'Accept-Encoding':'gzip, deflate'})
    payload = json_module.dumps(json).encode() if json is not None else None
    if payload is not None:
        if len(payload) > 128_000:
            raise BodyLimitError('request payload too large')
        original_headers['Content-Type'] = 'application/json'
    seen = set()
    telemetry = TransportTelemetry()
    for _ in range(5):
        if current in seen:
            exc = ValueError('redirect loop')
            exc.acquisition = asdict(telemetry)
            raise exc
        seen.add(current)
        try:
            pinned, host, host_headers, extensions = _pinned_target(current)
        except Exception as exc:
            exc.acquisition = asdict(telemetry)
            raise
        outgoing = {k:v for k,v in original_headers.items() if k.lower() != 'host'}
        outgoing.update(host_headers)
        wire_request = httpx.Request(method,pinned,headers=outgoing,content=payload,extensions=extensions)
        with httpx.Client(timeout=timeout,follow_redirects=False,trust_env=False) as client:
            telemetry.http_attempts += 1
            try:
                response = client.send(wire_request,stream=True)
            except Exception as exc:
                exc.acquisition = asdict(telemetry)
                raise
            try:
                if response.status_code in {301,302,303,307,308} and follow_redirects:
                    if method.upper() != 'GET':
                        raise ValueError('provider POST redirects are not permitted')
                    location = response.headers.get('location')
                    if not location:
                        raise ValueError('redirect without location')
                    target = urljoin(current,location)
                    previous, following = httpx.URL(current), httpx.URL(target)
                    if previous.scheme == 'https' and following.scheme != 'https':
                        raise ValueError('HTTPS redirect downgrade is not permitted')
                    if ((previous.scheme, previous.host, previous.port) !=
                            (following.scheme, following.host, following.port)):
                        # Provider credentials have arbitrary header names. Retain only
                        # explicitly origin-independent HTTP negotiation, never a secret
                        # denylist that silently misses a new provider credential field.
                        original_headers = {k:v for k,v in original_headers.items()
                            if k.lower() in {'accept','accept-language','accept-encoding','user-agent'}}
                    current = target
                    telemetry.redirects += 1
                    continue
                body = read_bounded(response,limit,telemetry)
                # Content is already decoded under the bound. Never ask HTTPX to
                # automatically decode it again when constructing the retained value.
                clean_headers = {k:v for k,v in response.headers.items()
                                 if k.lower() not in {'content-encoding','content-length'}}
                return httpx.Response(response.status_code,headers=clean_headers,content=body,
                    request=httpx.Request(method,current),
                    extensions={'acquisition':asdict(telemetry)})
            except Exception as exc:
                exc.acquisition = asdict(telemetry)
                raise
            finally:
                response.close()
    exc = ValueError('too many redirects')
    exc.acquisition = asdict(telemetry)
    raise exc
