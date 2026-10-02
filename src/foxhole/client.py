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
    "FoxholeMCP/0.1.0 (+https://github.com/gavmor/foxhole-mcp; automated-game-assistant)",
)


class WikiError(Exception):
    """Base exception for Foxhole MediaWiki client operations."""


class WikiAuthenticationError(WikiError):
    """Raised when authentication with MediaWiki fails."""


class WikiEditError(WikiError):
    """Raised when a MediaWiki page edit or creation fails."""

    def __init__(self, message: str, code: str | None = None, info: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.info = info


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
        username: str | None = None,
        password: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_url = api_url
        self.user_agent = user_agent
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.username = (
            username or os.getenv("FOXHOLE_WIKI_USERNAME") or os.getenv("MEDIAWIKI_USERNAME")
        )
        self.password = (
            password or os.getenv("FOXHOLE_WIKI_PASSWORD") or os.getenv("MEDIAWIKI_PASSWORD")
        )
        self._cache: dict[str, CacheEntry] = {}
        self._client: httpx.AsyncClient | None = client
        self._custom_client: bool = client is not None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._csrf_token: str | None = None
        self._is_logged_in: bool = False
        self._user_info: dict[str, Any] | None = None

    @property
    def is_logged_in(self) -> bool:
        """Whether the client is currently authenticated with MediaWiki."""
        return self._is_logged_in

    @property
    def user_info(self) -> dict[str, Any] | None:
        """Information about the currently authenticated user if logged in."""
        return self._user_info

    async def _get_client(self) -> httpx.AsyncClient:
        if self._custom_client and self._client is not None:
            return self._client

        current_loop = asyncio.get_running_loop()
        if self._client is None or self._client.is_closed or self._loop is not current_loop:
            self._loop = current_loop
            self._csrf_token = None
            self._is_logged_in = False
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
        """Close the underlying HTTP client and reset session state."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        if not self._custom_client:
            self._client = None
        self._csrf_token = None
        self._is_logged_in = False
        self._user_info = None

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

    async def cargo_query(
        self,
        table: str,
        fields: list[str],
        where: str | None = None,
        page_size: int = 500,
    ) -> list[dict[str, str]]:
        """Fetch every row of a Cargo table (action=cargoquery), paginating with offset.

        Unlike the page helpers, errors are raised rather than swallowed: a partial table
        would silently corrupt anything built from it. The API reports field names with
        underscores turned into spaces; they are restored so rows match the infobox keys.
        """
        client = await self._get_client()
        rows: list[dict[str, str]] = []
        offset = 0
        while True:
            params: dict[str, Any] = {
                "action": "cargoquery",
                "tables": table,
                "fields": ",".join(fields),
                "limit": page_size,
                "offset": offset,
                "format": "json",
            }
            if where:
                params["where"] = where
            try:
                resp = await client.get(self.api_url, params=params)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                raise WikiError(f"Cargo query on '{table}' failed: {e}") from e
            if "error" in data:
                raise WikiError(
                    f"Cargo query on '{table}' failed: {data['error'].get('info', data['error'])}"
                )
            batch = [
                {key.replace(" ", "_"): value for key, value in entry["title"].items()}
                for entry in data.get("cargoquery", [])
            ]
            rows.extend(batch)
            if len(batch) < page_size:
                return rows
            offset += page_size

    async def cargo_fields(self, table: str) -> list[str]:
        """List the field names of a Cargo table (action=cargofields)."""
        client = await self._get_client()
        params = {"action": "cargofields", "table": table, "format": "json"}
        try:
            resp = await client.get(self.api_url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise WikiError(f"Listing fields of '{table}' failed: {e}") from e
        if "error" in data:
            raise WikiError(
                f"Listing fields of '{table}' failed: {data['error'].get('info', data['error'])}"
            )
        return list(data.get("cargofields", {}))

    async def get_login_token(self) -> str:
        """Fetch a login token from MediaWiki (action=query&meta=tokens&type=login)."""
        client = await self._get_client()
        params = {
            "action": "query",
            "meta": "tokens",
            "type": "login",
            "format": "json",
        }
        try:
            resp = await client.get(self.api_url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise WikiAuthenticationError(
                f"HTTP request failed while fetching login token: {e}"
            ) from e

        if "error" in data:
            info = data["error"].get("info", data["error"].get("code", "Unknown error"))
            raise WikiAuthenticationError(f"API error fetching login token: {info}")

        token = data.get("query", {}).get("tokens", {}).get("logintoken")
        if not token:
            raise WikiAuthenticationError("No login token returned in MediaWiki tokens response")
        return token

    async def login(self, username: str | None = None, password: str | None = None) -> bool:
        """Authenticate with MediaWiki using credentials or bot password."""
        user = username or self.username
        pwd = password or self.password
        if not user or not pwd:
            raise WikiAuthenticationError("Username and password must be provided to authenticate")

        client = await self._get_client()
        login_token = await self.get_login_token()

        payload = {
            "action": "login",
            "lgname": user,
            "lgpassword": pwd,
            "lgtoken": login_token,
            "format": "json",
        }
        try:
            resp = await client.post(self.api_url, data=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise WikiAuthenticationError(f"HTTP request failed during login: {e}") from e

        if "error" in data:
            info = data["error"].get("info", data["error"].get("code", "Unknown error"))
            raise WikiAuthenticationError(f"Login error from API: {info}")

        login_res = data.get("login", {})
        result = login_res.get("result")

        if result == "Success":
            self._is_logged_in = True
            self._user_info = {
                "user_id": login_res.get("lguserid"),
                "username": login_res.get("lgusername"),
            }
            self._csrf_token = None
            logger.info(
                "Successfully authenticated with MediaWiki as %s", self._user_info["username"]
            )
            return True
        else:
            self._is_logged_in = False
            self._user_info = None
            reason = login_res.get("reason", f"Login failed with status '{result}'")
            raise WikiAuthenticationError(f"MediaWiki authentication failed: {reason}")

    async def get_csrf_token(self, force_refresh: bool = False) -> str:
        """Fetch a CSRF token for editing or write actions, caching it for subsequent calls."""
        if not force_refresh and self._csrf_token:
            return self._csrf_token

        if not self._is_logged_in and self.username and self.password:
            await self.login()

        client = await self._get_client()
        params = {
            "action": "query",
            "meta": "tokens",
            "type": "csrf",
            "format": "json",
        }
        try:
            resp = await client.get(self.api_url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise WikiError(f"HTTP request failed while fetching CSRF token: {e}") from e

        if "error" in data:
            info = data["error"].get("info", data["error"].get("code", "Unknown error"))
            raise WikiError(f"API error fetching CSRF token: {info}")

        csrftoken = data.get("query", {}).get("tokens", {}).get("csrftoken")
        if not csrftoken:
            raise WikiError("No CSRF token returned in MediaWiki tokens response")

        self._csrf_token = csrftoken
        return self._csrf_token

    async def edit(
        self,
        title: str,
        text: str | None = None,
        summary: str = "",
        section: str | int | None = None,
        sectiontitle: str | None = None,
        appendtext: str | None = None,
        prependtext: str | None = None,
        minor: bool = False,
        bot: bool = False,
        createonly: bool = False,
        nocreate: bool = False,
        baserevid: int | None = None,
    ) -> dict[str, Any]:
        """Create or edit a wiki page using MediaWiki action=edit.

        Automatically retrieves CSRF token and retries once on token expiration (badtoken).
        Invalidates cached page content upon successful edit.
        """
        token = await self.get_csrf_token()

        payload: dict[str, Any] = {
            "action": "edit",
            "title": title,
            "summary": summary,
            "token": token,
            "format": "json",
        }

        if text is not None:
            payload["text"] = text
        if appendtext is not None:
            payload["appendtext"] = appendtext
        if prependtext is not None:
            payload["prependtext"] = prependtext
        if section is not None:
            payload["section"] = str(section)
        if sectiontitle is not None:
            payload["sectiontitle"] = sectiontitle
        if minor:
            payload["minor"] = "1"
        if bot:
            payload["bot"] = "1"
        if createonly:
            payload["createonly"] = "1"
        if nocreate:
            payload["nocreate"] = "1"
        if baserevid is not None:
            payload["baserevid"] = str(baserevid)

        client = await self._get_client()

        try:
            resp = await client.post(self.api_url, data=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise WikiEditError(f"HTTP request failed during edit of '{title}': {e}") from e

        # Handle badtoken: refresh CSRF token and retry once
        if "error" in data and data["error"].get("code") in ("badtoken", "invalidtoken"):
            logger.warning(
                "CSRF token expired or invalid (%s) when editing '%s'. Refreshing token and retrying...",
                data["error"].get("code"),
                title,
            )
            token = await self.get_csrf_token(force_refresh=True)
            payload["token"] = token
            try:
                resp = await client.post(self.api_url, data=payload)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as e:
                raise WikiEditError(
                    f"HTTP request failed during retry edit of '{title}': {e}"
                ) from e

        if "error" in data:
            code = data["error"].get("code", "unknown")
            info = data["error"].get("info", "Unknown wiki error")
            raise WikiEditError(f"Wiki edit failed: [{code}] {info}", code=code, info=info)

        edit_data = data.get("edit", {})
        result_status = edit_data.get("result")

        # Invalidate page cache if successful
        if result_status == "Success":
            resolved_title = edit_data.get("title", title)
            self._cache.pop(f"page:{title.lower()}", None)
            self._cache.pop(f"page:{resolved_title.lower()}", None)

        return edit_data
