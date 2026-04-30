import json
import socket

import httpx

from tools import curl_tool, url_safety


def _public_dns(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def _private_dns(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    class _Stream:
        def __init__(self, response):
            self.response = response

        def __enter__(self):
            return self.response

        def __exit__(self, *_args):
            return False

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self._Stream(self.responses.pop(0))


def test_curl_tool_blocks_file_scheme():
    result = json.loads(curl_tool.curl_tool("file:///etc/passwd"))

    assert result["success"] is False
    assert result["blocked"] is True
    assert "Only http:// and https://" in result["error"]


def test_curl_tool_blocks_private_address(monkeypatch):
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _private_dns)
    monkeypatch.setattr(url_safety, "_global_allow_private_urls", lambda: False)

    result = json.loads(curl_tool.curl_tool("http://127.0.0.1:8080/secret"))

    assert result["success"] is False
    assert result["blocked"] is True
    assert "private/internal" in result["error"]


def test_curl_tool_allows_private_address_when_config_allows(monkeypatch):
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _private_dns)
    monkeypatch.setattr(url_safety, "_global_allow_private_urls", lambda: True)
    monkeypatch.setattr(curl_tool, "check_website_access", lambda _url: None)

    request = httpx.Request("GET", "http://127.0.0.1:8080/ok")
    response = httpx.Response(200, content=b"local ok", request=request)
    fake_client = _FakeClient([response])
    monkeypatch.setattr(curl_tool.httpx, "Client", lambda *_args, **_kwargs: fake_client)

    result = json.loads(curl_tool.curl_tool("http://127.0.0.1:8080/ok"))

    assert result["success"] is True
    assert result["body"] == "local ok"


def test_curl_tool_returns_public_http_response(monkeypatch):
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _public_dns)
    monkeypatch.setattr(url_safety, "_global_allow_private_urls", lambda: False)
    monkeypatch.setattr(curl_tool, "check_website_access", lambda _url: None)

    request = httpx.Request("GET", "https://example.com/data")
    response = httpx.Response(
        200,
        content=b"hello",
        headers={"content-type": "text/plain", "set-cookie": "secret=1"},
        request=request,
    )
    fake_client = _FakeClient([response])
    monkeypatch.setattr(curl_tool.httpx, "Client", lambda *_args, **_kwargs: fake_client)

    result = json.loads(curl_tool.curl_tool("https://example.com/data"))

    assert result["success"] is True
    assert result["status_code"] == 200
    assert result["body"] == "hello"
    assert "set-cookie" not in result["headers"]
    assert fake_client.calls[0][0] == "GET"


def test_curl_tool_validates_redirect_targets(monkeypatch):
    def _dns(hostname, *_args, **_kwargs):
        if hostname == "example.com":
            return _public_dns()
        return _private_dns()

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _dns)
    monkeypatch.setattr(url_safety, "_global_allow_private_urls", lambda: False)
    monkeypatch.setattr(curl_tool, "check_website_access", lambda _url: None)

    request = httpx.Request("GET", "https://example.com/start")
    response = httpx.Response(
        302,
        headers={"location": "http://127.0.0.1/secret"},
        request=request,
    )
    fake_client = _FakeClient([response])
    monkeypatch.setattr(curl_tool.httpx, "Client", lambda *_args, **_kwargs: fake_client)

    result = json.loads(
        curl_tool.curl_tool("https://example.com/start", follow_redirects=True)
    )

    assert result["success"] is False
    assert result["blocked"] is True
    assert "private/internal" in result["error"]
    assert len(fake_client.calls) == 1


def test_curl_tool_truncates_response_without_exceeding_max_bytes(monkeypatch):
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _public_dns)
    monkeypatch.setattr(url_safety, "_global_allow_private_urls", lambda: False)
    monkeypatch.setattr(curl_tool, "check_website_access", lambda _url: None)

    request = httpx.Request("GET", "https://example.com/big")
    response = httpx.Response(200, content=b"abcdef", request=request)
    fake_client = _FakeClient([response])
    monkeypatch.setattr(curl_tool.httpx, "Client", lambda *_args, **_kwargs: fake_client)

    result = json.loads(curl_tool.curl_tool("https://example.com/big", max_bytes=3))

    assert result["success"] is True
    assert result["body"] == "abc"
    assert result["truncated"] is True
