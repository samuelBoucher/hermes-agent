"""Native no-key web extraction backend.

This provider is intentionally small: it fetches public HTTP(S) URLs directly
and extracts readable text from HTML with the Python stdlib. It is not a
replacement for Firecrawl/Tavily/Exa on hostile JS-heavy sites, but it gives
Hermes a zero-credit ``web_extract`` path for normal static pages.
"""

from __future__ import annotations

from plugins.web.native.provider import NativeWebExtractProvider


def register(ctx) -> None:
    """Register the native extract provider with the plugin context."""
    ctx.register_web_search_provider(NativeWebExtractProvider())
