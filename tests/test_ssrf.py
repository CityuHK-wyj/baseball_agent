import pytest

from app.capabilities.web_search import SSRFError, assert_safe_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://10.0.0.5/",
        "http://192.168.1.10/",
        "http://172.16.5.4/",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "gopher://example.com/",
        "http://internal.local/",
        "http://example.com:8080/",
    ],
)
def test_blocked_urls(url):
    with pytest.raises(SSRFError):
        assert_safe_url(url)


def test_public_https_allowed():
    # DNS-based; skip if the environment cannot resolve.
    try:
        assert assert_safe_url("https://example.com/") == "https://example.com/"
    except SSRFError as exc:  # pragma: no cover - offline environment
        pytest.skip(f"DNS unavailable: {exc}")
