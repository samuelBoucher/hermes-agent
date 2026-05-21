"""Native no-key content extraction backend for Hermes web_extract."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx

from agent.web_search_provider import WebSearchProvider
from tools.url_safety import is_safe_url
from tools.website_policy import check_website_access

logger = logging.getLogger(__name__)

_USER_AGENT = "HermesAgent-NativeExtract/1.0 (+https://github.com/NousResearch/hermes-agent)"
_DEFAULT_TIMEOUT = 20.0
_MAX_REDIRECTS = 5
_MAX_CONTENT_CHARS = 1_000_000

_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}


@dataclass
class _FetchedPage:
    url: str
    final_url: str
    status_code: int
    content_type: str
    text: str


class _ReadableHTMLParser(HTMLParser):
    """Very small HTML-to-text extractor using only stdlib pieces."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: List[str] = []
        self.text_parts: List[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = unescape(data or "")
        if not text.strip():
            return
        if self._in_title:
            self.title_parts.append(text)
        else:
            self.text_parts.append(text)

    @property
    def title(self) -> str:
        return _collapse_whitespace(" ".join(self.title_parts)).strip()

    @property
    def content(self) -> str:
        return _normalize_text(" ".join(self.text_parts))


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", text or "")


def _normalize_text(text: str) -> str:
    text = _collapse_whitespace(text)
    text = re.sub(r"\s+([,.;:!?%])", r"\1", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_title_from_text(text: str) -> str:
    for line in (text or "").splitlines():
        candidate = _collapse_whitespace(line).strip()
        if candidate:
            return candidate[:180]
    return ""


def _html_to_text(html: str) -> tuple[str, str]:
    parser = _ReadableHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.title, parser.content


class NativeWebExtractProvider(WebSearchProvider):
    """No-key direct HTTP(S) content extraction provider."""

    @property
    def name(self) -> str:
        return "native"

    @property
    def display_name(self) -> str:
        return "Native HTTP Extract"

    def is_available(self) -> bool:
        return True

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Native HTTP Extract",
            "badge": "free · no key · extract only",
            "tag": "Direct HTTP fetch + lightweight HTML text extraction; pair with xAI/ddgs/Brave/SearXNG for search.",
            "env_vars": [],
        }

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        max_chars = _coerce_positive_int(kwargs.get("max_chars"), _MAX_CONTENT_CHARS)
        timeout = _coerce_positive_float(kwargs.get("timeout"), _DEFAULT_TIMEOUT)
        results: List[Dict[str, Any]] = []
        with httpx.Client(
            follow_redirects=False,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,text/plain,application/xhtml+xml;q=0.9,*/*;q=0.2"},
        ) as client:
            for url in urls:
                results.append(self._extract_one(client, str(url), max_chars=max_chars))
        return results

    def _extract_one(self, client: httpx.Client, url: str, *, max_chars: int) -> Dict[str, Any]:
        try:
            fetched = _fetch_public_url(client, url, max_chars=max_chars)
            title, content = _content_to_text(fetched)
            if not title:
                title = _extract_title_from_text(content)
            if not content:
                return _error_result(url, "Native extractor found no readable text", final_url=fetched.final_url)
            return {
                "url": url,
                "title": title,
                "content": content,
                "raw_content": content,
                "metadata": {
                    "sourceURL": url,
                    "finalURL": fetched.final_url,
                    "statusCode": fetched.status_code,
                    "contentType": fetched.content_type,
                    "provider": self.name,
                },
            }
        except Exception as exc:  # noqa: BLE001 — per-URL errors belong in results[]
            logger.info("Native extract failed for %s: %s", url, exc)
            return _error_result(url, str(exc))


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _coerce_positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _fetch_public_url(client: httpx.Client, url: str, *, max_chars: int) -> _FetchedPage:
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        if not is_safe_url(current):
            raise ValueError("Blocked: URL targets a private or internal network address")
        blocked = check_website_access(current)
        if blocked:
            raise ValueError(blocked.get("message") or "Blocked by website policy")

        response = client.get(current)
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                raise ValueError(f"Redirect response from {current} had no Location header")
            current = urljoin(str(response.url), location)
            continue

        response.raise_for_status()
        text = response.text
        if len(text) > max_chars:
            text = text[:max_chars]
        return _FetchedPage(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            content_type=response.headers.get("content-type", ""),
            text=text,
        )
    raise ValueError(f"Too many redirects (>{_MAX_REDIRECTS})")


def _content_to_text(page: _FetchedPage) -> tuple[str, str]:
    content_type = (page.content_type or "").split(";", 1)[0].strip().lower()
    if content_type in {"text/html", "application/xhtml+xml", ""}:
        return _html_to_text(page.text)
    if content_type.startswith("text/") or content_type in {"application/json", "application/xml", "text/xml"}:
        content = _normalize_text(page.text)
        return _extract_title_from_text(content), content
    raise ValueError(f"Unsupported content type for native extraction: {content_type or 'unknown'}")


def _error_result(url: str, error: str, *, final_url: str = "") -> Dict[str, Any]:
    return {
        "url": url,
        "title": "",
        "content": "",
        "error": error,
        "metadata": {"sourceURL": url, **({"finalURL": final_url} if final_url else {})},
    }
