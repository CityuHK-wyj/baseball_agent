"""Minimal HTTP fetching with retries, for live knowledge refresh.

Uses the standard library so ingestion has no extra runtime dependency. Failures are
explicit: a refresh never mutates the store when its source cannot be reached.
"""

import json
import time
import urllib.error
import urllib.request

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36")


class FetchError(RuntimeError):
    pass


def fetch(url: str, *, timeout: float = 30, retries: int = 3, pause: float = 2.0,
          user_agent: str = DEFAULT_USER_AGENT) -> bytes:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(url, headers={"User-Agent": user_agent,
                                                       "Accept": "*/*"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as error:
            last = error
            if attempt < retries:
                time.sleep(pause * attempt)
    raise FetchError(f"could not fetch {url!r}: {type(last).__name__}")


def fetch_json(url: str, **kwargs) -> dict:
    return json.loads(fetch(url, **kwargs).decode("utf-8"))
