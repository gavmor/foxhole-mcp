"""War API client supporting all Foxhole shards, ETags, and caching."""

import asyncio
import logging
import os
import time
from typing import Any

import httpx

from foxhole.warapi.constants import DEFAULT_SHARD, SHARDS
from foxhole.warapi.models import (
    GlobalCasualties,
    MapData,
    MapItem,
    MapTextItem,
    VictoryTownStatus,
    WarReport,
    WarState,
)

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = os.getenv(
    "FOXHOLE_USER_AGENT",
    "FoxholeMCP/0.1.0 (+https://github.com/gavmor/foxhole-mcp; game-assistant)",
)


class CachedResponse:
    """Store response payload alongside its ETag and expiration."""

    def __init__(self, data: Any, etag: str | None = None, ttl_seconds: float = 60.0):
        self.data = data
        self.etag = etag
        self.expires_at = time.monotonic() + ttl_seconds

    def is_fresh(self) -> bool:
        return time.monotonic() < self.expires_at


class WarApiClient:
    """Async client for querying the official Foxhole War API (worldconquest)."""

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 15.0,
    ):
        self.user_agent = user_agent
        self.timeout = timeout
        self._cache: dict[str, CachedResponse] = {}
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _resolve_base_url(self, shard: str) -> str:
        shard_key = shard.lower().strip()
        if shard_key in SHARDS:
            return SHARDS[shard_key]
        if shard_key.startswith("http://") or shard_key.startswith("https://"):
            return shard_key.rstrip("/")
        return SHARDS[DEFAULT_SHARD]

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

    async def _request(
        self,
        endpoint: str,
        shard: str = DEFAULT_SHARD,
        ttl_seconds: float = 60.0,
    ) -> Any | None:
        """Make an HTTP GET request with ETag support and caching."""
        base_url = self._resolve_base_url(shard)
        url = f"{base_url}{endpoint}"
        cache_key = f"{shard}:{endpoint}"

        cached = self._cache.get(cache_key)
        # If cache entry is fresh and unexpired, return immediately
        if cached and cached.is_fresh():
            return cached.data

        client = await self._get_client()
        headers = {}
        if cached and cached.etag:
            headers["If-None-Match"] = cached.etag

        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 304 and cached:
                # Refresh expiration and return cached data
                cached.expires_at = time.monotonic() + ttl_seconds
                return cached.data

            resp.raise_for_status()
            data = resp.json()
            etag = resp.headers.get("etag")
            self._cache[cache_key] = CachedResponse(data=data, etag=etag, ttl_seconds=ttl_seconds)
            return data
        except httpx.HTTPStatusError as e:
            logger.error("War API error (%s) on %s: %s", e.response.status_code, url, e)
            if cached:
                return cached.data
            return None
        except httpx.HTTPError as e:
            logger.error("War API connection failed on %s: %s", url, e)
            if cached:
                return cached.data
            return None

    async def get_war_state(self, shard: str = DEFAULT_SHARD) -> WarState | None:
        """Fetch current World Conquest state for the shard."""
        data = await self._request("/worldconquest/war", shard=shard, ttl_seconds=30.0)
        if not data:
            return None
        return WarState.model_validate(data)

    async def get_maps(self, shard: str = DEFAULT_SHARD) -> list[str]:
        """Fetch list of all active World Conquest map names (excluding home regions)."""
        data = await self._request("/worldconquest/maps", shard=shard, ttl_seconds=300.0)
        if not data or not isinstance(data, list):
            return []
        # Filter out HomeRegionC and HomeRegionW which do not have battle map data
        return [m for m in data if m not in ("HomeRegionC", "HomeRegionW")]

    async def get_war_report(self, map_name: str, shard: str = DEFAULT_SHARD) -> WarReport | None:
        """Fetch casualty and enlistment report for a specific map."""
        data = await self._request(
            f"/worldconquest/warReport/{map_name}",
            shard=shard,
            ttl_seconds=10.0,
        )
        if not data:
            return None
        return WarReport(
            map_name=map_name,
            totalEnlistments=data.get("totalEnlistments", 0),
            colonialCasualties=data.get("colonialCasualties", 0),
            wardenCasualties=data.get("wardenCasualties", 0),
            dayOfWar=data.get("dayOfWar", 0),
            version=data.get("version", 0),
        )

    async def get_static_map_data(
        self, map_name: str, shard: str = DEFAULT_SHARD
    ) -> MapData | None:
        """Fetch unchanging map items (world structures, resource nodes, labels)."""
        data = await self._request(
            f"/worldconquest/maps/{map_name}/static",
            shard=shard,
            ttl_seconds=3600.0,  # Static data rarely changes within a war
        )
        if not data:
            return None

        map_items = [MapItem.from_api_item(item) for item in data.get("mapItems", [])]
        text_items = [MapTextItem.model_validate(item) for item in data.get("mapTextItems", [])]

        return MapData(
            map_name=map_name,
            regionId=data.get("regionId", 0),
            scorchedVictoryTowns=data.get("scorchedVictoryTowns", 0),
            map_items=map_items,
            map_text_items=text_items,
            lastUpdated=data.get("lastUpdated"),
            version=data.get("version", 0),
        )

    async def get_dynamic_map_data(
        self, map_name: str, shard: str = DEFAULT_SHARD
    ) -> MapData | None:
        """Fetch dynamic base ownership and objective capture states."""
        data = await self._request(
            f"/worldconquest/maps/{map_name}/dynamic/public",
            shard=shard,
            ttl_seconds=15.0,
        )
        if not data:
            return None

        map_items = [MapItem.from_api_item(item) for item in data.get("mapItems", [])]
        text_items = [MapTextItem.model_validate(item) for item in data.get("mapTextItems", [])]

        return MapData(
            map_name=map_name,
            regionId=data.get("regionId", 0),
            scorchedVictoryTowns=data.get("scorchedVictoryTowns", 0),
            map_items=map_items,
            map_text_items=text_items,
            lastUpdated=data.get("lastUpdated"),
            version=data.get("version", 0),
        )

    async def get_global_casualties(
        self,
        shard: str = DEFAULT_SHARD,
        top_n: int = 5,
    ) -> GlobalCasualties | None:
        """Aggregate casualties and enlistments across all active map hexes."""
        maps = await self.get_maps(shard=shard)
        if not maps:
            return None

        # Concurrently fetch war reports for all hexes
        tasks = [self.get_war_report(m, shard=shard) for m in maps]
        reports: list[WarReport | None] = await asyncio.gather(*tasks)

        valid_reports = [r for r in reports if r is not None]
        if not valid_reports:
            return None

        total_enlistments = sum(r.total_enlistments for r in valid_reports)
        col_casualties = sum(r.colonial_casualties for r in valid_reports)
        war_casualties = sum(r.warden_casualties for r in valid_reports)
        max_day = max(r.day_of_war for r in valid_reports) if valid_reports else 0

        # Sort frontlines by total casualties
        sorted_fronts = sorted(valid_reports, key=lambda r: r.total_casualties, reverse=True)
        top_fronts = [
            {
                "map_name": r.map_name,
                "colonial_casualties": r.colonial_casualties,
                "warden_casualties": r.warden_casualties,
                "total_casualties": r.total_casualties,
            }
            for r in sorted_fronts[:top_n]
        ]

        return GlobalCasualties(
            shard=shard,
            day_of_war=max_day,
            total_enlistments=total_enlistments,
            colonial_casualties=col_casualties,
            warden_casualties=war_casualties,
            total_casualties=col_casualties + war_casualties,
            casualty_diff=war_casualties - col_casualties,
            most_active_fronts=top_fronts,
        )

    async def get_victory_town_status(self, shard: str = DEFAULT_SHARD) -> VictoryTownStatus | None:
        """Calculate captured victory towns across all maps for each faction."""
        war_state = await self.get_war_state(shard=shard)
        if not war_state:
            return None

        maps = await self.get_maps(shard=shard)
        if not maps:
            return None

        tasks = [self.get_dynamic_map_data(m, shard=shard) for m in maps]
        map_data_list: list[MapData | None] = await asyncio.gather(*tasks)

        warden_vps = 0
        colonial_vps = 0
        scorched_vps = 0
        unclaimed_vps = 0

        for md in map_data_list:
            if not md:
                continue
            scorched_vps += md.scorched_victory_towns
            for item in md.map_items:
                if item.is_victory_base:
                    if item.is_scorched:
                        scorched_vps += 1
                    elif item.team_id == "WARDENS":
                        warden_vps += 1
                    elif item.team_id == "COLONIALS":
                        colonial_vps += 1
                    else:
                        unclaimed_vps += 1

        effective_req = max(0, war_state.required_victory_towns - scorched_vps)

        return VictoryTownStatus(
            shard=shard,
            required_to_win=war_state.required_victory_towns,
            effective_required_to_win=effective_req,
            warden_captured=warden_vps,
            colonial_captured=colonial_vps,
            scorched_count=scorched_vps,
            unclaimed_or_neutral=unclaimed_vps,
        )
