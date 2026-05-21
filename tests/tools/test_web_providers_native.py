"""Tests for the native no-key web extraction provider."""

from __future__ import annotations

import json

import httpx

from plugins.web.native.provider import NativeWebExtractProvider


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requested = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url):
        self.requested.append(url)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _response(url, body, *, status_code=200, content_type="text/html", headers=None):
    request = httpx.Request("GET", url)
    return httpx.Response(
        status_code,
        request=request,
        headers={"content-type": content_type, **(headers or {})},
        text=body,
    )


def test_native_provider_extracts_readable_html(monkeypatch):
    fake = _FakeClient([
        _response(
            "https://example.com/page",
            """
            <html><head><title>Demo Page</title><script>ignore()</script></head>
            <body><h1>Hello</h1><p>Readable <b>content</b>.</p></body></html>
            """,
        )
    ])
    monkeypatch.setattr("plugins.web.native.provider.httpx.Client", lambda **kwargs: fake)
    monkeypatch.setattr("plugins.web.native.provider.is_safe_url", lambda url: True)
    monkeypatch.setattr("plugins.web.native.provider.check_website_access", lambda url: None)

    provider = NativeWebExtractProvider()
    result = provider.extract(["https://example.com/page"])[0]

    assert result["url"] == "https://example.com/page"
    assert result["title"] == "Demo Page"
    assert "Hello" in result["content"]
    assert "Readable content." in result["content"]
    assert "ignore" not in result["content"]
    assert result["metadata"]["provider"] == "native"


def test_native_provider_rechecks_redirect_targets(monkeypatch):
    fake = _FakeClient([
        _response(
            "https://example.com/start",
            "",
            status_code=302,
            headers={"location": "http://127.0.0.1/admin"},
        )
    ])
    checked = []

    def safe(url):
        checked.append(url)
        return not url.startswith("http://127.0.0.1")

    monkeypatch.setattr("plugins.web.native.provider.httpx.Client", lambda **kwargs: fake)
    monkeypatch.setattr("plugins.web.native.provider.is_safe_url", safe)
    monkeypatch.setattr("plugins.web.native.provider.check_website_access", lambda url: None)

    result = NativeWebExtractProvider().extract(["https://example.com/start"])[0]

    assert "private or internal" in result["error"]
    assert checked == ["https://example.com/start", "http://127.0.0.1/admin"]


def test_get_extract_backend_accepts_native(monkeypatch):
    from tools import web_tools

    monkeypatch.setattr(web_tools, "_load_web_config", lambda: {"extract_backend": "native"})

    assert web_tools._get_extract_backend() == "native"


def test_web_extract_can_use_native_backend(monkeypatch):
    import asyncio

    from agent.web_search_registry import _reset_for_tests, register_provider
    from tools import web_tools

    monkeypatch.setattr(web_tools, "_load_web_config", lambda: {"extract_backend": "native"})
    monkeypatch.setattr(web_tools, "check_auxiliary_model", lambda: False)
    monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False, raising=False)
    monkeypatch.setattr("tools.web_tools.is_safe_url", lambda url: True)
    monkeypatch.setattr("plugins.web.native.provider.is_safe_url", lambda url: True)
    monkeypatch.setattr("plugins.web.native.provider.check_website_access", lambda url: None)

    fake = _FakeClient([
        _response("https://example.com", "<title>Example</title><p>Native works.</p>")
    ])
    monkeypatch.setattr("plugins.web.native.provider.httpx.Client", lambda **kwargs: fake)

    _reset_for_tests()
    register_provider(NativeWebExtractProvider())
    try:
        result_str = asyncio.get_event_loop().run_until_complete(
            web_tools.web_extract_tool(["https://example.com"], use_llm_processing=False)
        )
    finally:
        _reset_for_tests()

    result = json.loads(result_str)
    assert result["results"][0]["title"] == "Example"
    assert "Native works." in result["results"][0]["content"]
