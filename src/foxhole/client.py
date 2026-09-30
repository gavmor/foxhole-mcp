import asyncio
import logging
import os
import time
from typing import Any

import httpx

from foxhole.models import SearchResult

# Silence HTTP request info logs from httpx and httpcore
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://foxhole.wiki.gg/api.php"
DEFAULT_USER_AGENT = os.getenv(
    "FOXHOLE_USER_AGENT",
    "FoxholeMCP/0.1.0 (+https://github.com/foxhole/foxhole-mcp; automated-game-assistant)",
)


class CacheEntry:
    """Simple cache entry with expiration timestamp."""

    def __init__(self, data: Any, ttl_seconds: float = 600.0):
        self.data = data
        self.expires_at = time.monotonic() + ttl_seconds

    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at


class FoxholeWikiClient:
    """Asynchronous client for interacting with the Foxhole MediaWiki API."""

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 15.0,
        cache_ttl: float = 600.0,
    ):
        self.api_url = api_url
        self.user_agent = user_agent
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._cache: dict[str, CacheEntry] = {}
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        current_loop = asyncio.get_running_loop()
        if self._client is None or self._client.is_closed or self._loop is not current_loop:
            self._loop = current_loop
            headers = {
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            }
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def _get_from_cache(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.is_expired():
            del self._cache[key]
            return None
        return entry.data

    def _set_cache(self, key: str, data: Any) -> None:
        self._cache[key] = CacheEntry(data, self.cache_ttl)

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        """Perform a full-text search on foxhole.wiki.gg."""
        cache_key = f"search:{query.lower()}:{limit}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        client = await self._get_client()
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
            "format": "json",
        }

        try:
            resp = await client.get(self.api_url, params=params)
            resp.raise_for_status()
            data = resp.json()
            search_items = data.get("query", {}).get("search", [])

            results = [
                SearchResult(
                    title=item["title"],
                    pageid=item["pageid"],
                    snippet=item.get("snippet", "")
                    .replace('<span class="searchmatch">', "")
                    .replace("</span>", ""),
                    wordcount=item.get("wordcount", 0),
                    url=f"https://foxhole.wiki.gg/wiki/{item['title'].replace(' ', '_')}",
                )
                for item in search_items
            ]
            self._set_cache(cache_key, results)
            return results
        except httpx.HTTPError as e:
            logger.error("Failed to search Foxhole wiki for '%s': %s", query, e)
            return []

    async def opensearch(self, query: str, limit: int = 5) -> list[str]:
        """Get title suggestions and prefix matches."""
        cache_key = f"opensearch:{query.lower()}:{limit}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        client = await self._get_client()
        params = {
            "action": "opensearch",
            "search": query,
            "limit": limit,
            "format": "json",
        }

        try:
            resp = await client.get(self.api_url, params=params)
            resp.raise_for_status()
            data = resp.json()
            # opensearch format: [query, [title1, title2, ...], ...]
            titles: list[str] = data[1] if len(data) > 1 and isinstance(data[1], list) else []
            self._set_cache(cache_key, titles)
            return titles
        except httpx.HTTPError as e:
            logger.error("Failed opensearch for '%s': %s", query, e)
            return []

    async def get_page_data(self, title: str) -> dict[str, Any] | None:
        """Fetch page wikitext, categories, and resolved title following redirects."""
        cache_key = f"page:{title.lower()}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        client = await self._get_client()
        params = {
            "action": "parse",
            "page": title,
            "redirects": "1",
            "prop": "wikitext|categories|sections",
            "format": "json",
        }

        try:
            resp = await client.get(self.api_url, params=params)
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                logger.warning("Wiki API error for '%s': %s", title, data["error"].get("info"))
                return None

            parse_data = data.get("parse", {})
            result = {
                "title": parse_data.get("title", title),
                "pageid": parse_data.get("pageid"),
                "wikitext": parse_data.get("wikitext", {}).get("*", ""),
                "categories": [c.get("*", "") for c in parse_data.get("categories", [])],
                "sections": [s.get("line", "") for s in parse_data.get("sections", [])],
            }
            self._set_cache(cache_key, result)
            return result
        except httpx.HTTPError as e:
            logger.error("Failed to fetch page data for '%s': %s", title, e)
            return None
