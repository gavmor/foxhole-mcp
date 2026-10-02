"""MCP tools for querying Foxhole live War API telemetry and world state."""

import logging
import re
from datetime import UTC, datetime
from typing import Any

from foxhole import ingame_time as igt
from foxhole.tools.base import BaseToolProvider
from foxhole.warapi import (
    DEFAULT_SHARD,
    ICON_CATEGORIES,
    WarApiClient,
)

logger = logging.getLogger(__name__)


class WarApiTools(BaseToolProvider):
    """Encapsulates War API tools and their associated client."""

    def __init__(self, war_client: WarApiClient | None = None) -> None:
        self.war_client = war_client or WarApiClient()

    async def close(self) -> None:
        """Close the underlying War API client."""
        await self.war_client.close()

    async def _clock(self, shard: str, report_day: int | None = None) -> igt.ClockState | None:
        """Load the in-game clock calibration, refreshed with a current dayOfWar."""
        war = await self.war_client.get_war_state(shard=shard)
        if not war or not war.conquest_start_time:
            return None
        state = igt.ensure_war(igt.load_state(shard), war.war_id, shard, war.conquest_start_time)
        if report_day is None:
            maps = await self.war_client.get_maps(shard=shard)
            report = await self.war_client.get_war_report(maps[0], shard=shard) if maps else None
            report_day = report.day_of_war if report else None
        if report_day:
            state = igt.observe_day(state, report_day, igt.now())
        igt.save_state(state)
        return state

    async def get_ingame_time(
        self, shard: str = DEFAULT_SHARD, at_utc: str | None = None
    ) -> dict[str, Any]:
        """Current in-game day and clock ("Day 27, 0627 Hours"), as on the Map Screen.

        One in-game day lasts one real hour. The day number comes from the War API's dayOfWar.
        The clock within the day comes from a calibration that tightens with every observation,
        and is exact once `calibrate_ingame_clock` has been given a reading from the game.
        `plus_minus_minutes` is the uncertainty in in-game minutes.

        Args:
            shard: Shard name (default: 'live-1')
            at_utc: Convert this UTC time instead of now (ISO 8601, e.g. '2026-10-02T18:48:00Z')
        """
        state = await self._clock(shard)
        if state is None:
            return {"error": f"No active war on shard '{shard}'."}
        t = igt.now()
        if at_utc:
            try:
                t = datetime.fromisoformat(at_utc.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return {"error": f"Bad at_utc {at_utc!r}; use ISO 8601"}
        clock = igt.to_ingame(state, t)
        return {
            "shard": shard,
            "utc": datetime.fromtimestamp(t, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
            **(clock.model_dump() if clock else {"label": None}),
            "calibration": {
                "observations": state.observations,
                "pinned_note": state.pinned_note,
                "hint": None
                if state.pinned is not None
                else "Exact once you give calibrate_ingame_clock the in-game time from the Map Screen.",
            },
        }

    async def calibrate_ingame_clock(
        self,
        day: int,
        time_hhmm: str,
        observed_at_utc: str | None = None,
        shard: str = DEFAULT_SHARD,
    ) -> dict[str, Any]:
        """Pin the in-game clock to a reading from the game's Map Screen (bottom left).

        Rejected if it contradicts the War API's dayOfWar. Needed once per war; afterwards every
        in-game time the server reports is exact to about a minute.

        Args:
            day: In-game day shown (e.g. 27)
            time_hhmm: In-game military time shown (e.g. '0627')
            observed_at_utc: When you read it (ISO 8601); omit if you read it just now
            shard: Shard name (default: 'live-1')
        """
        state = await self._clock(shard)
        if state is None:
            return {"error": f"No active war on shard '{shard}'."}
        t, slack = igt.now(), 5.0  # "just now": allow a few seconds to type it in
        if observed_at_utc:
            try:
                t = datetime.fromisoformat(observed_at_utc.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return {"error": f"Bad observed_at_utc {observed_at_utc!r}; use ISO 8601"}
            # A time given only to the minute is uncertain by up to half a minute either way
            slack = 1.0 if re.search(r"\d{2}:\d{2}:\d{2}", observed_at_utc) else 30.0
        try:
            state = igt.calibrate(
                state,
                day,
                time_hhmm.replace(":", ""),
                t,
                note=f"Day {day}, {time_hhmm} at {observed_at_utc or 'call time'}",
                real_seconds_uncertain=slack,
            )
        except ValueError as e:
            return {"error": str(e)}
        igt.save_state(state)
        clock = igt.to_ingame(state, igt.now())
        return {"calibrated": True, "now": clock.model_dump() if clock else None}

    async def get_war_status(self, shard: str = DEFAULT_SHARD) -> dict[str, Any]:
        """Query live World Conquest status, war number, active winner, and victory requirements.

        Args:
            shard: Target shard: 'live-1' (Able), 'live-2' (Baker), 'live-3' (Charlie), or 'dev'
        """
        state = await self.war_client.get_war_state(shard=shard)
        if not state:
            return {
                "error": f"Failed to retrieve War state from shard '{shard}'. Server may be offline."
            }
        clock = await self._clock(shard)

        out = {
            "shard": shard,
            "war_number": state.war_number,
            "war_id": state.war_id,
            "status": state.status_display,
            "is_active": state.is_active,
            "winner": state.winner,
            "now_ingame": self._label(clock, igt.now()),
            "start_time_ingame": self._label(clock, (state.conquest_start_time or 0) / 1000),
            "start_time_utc": state.start_datetime,
            "required_victory_towns": state.required_victory_towns,
            "short_required_victory_towns": state.short_required_victory_towns,
        }
        return out

    async def get_war_casualties(
        self, map_name: str | None = None, shard: str = DEFAULT_SHARD
    ) -> dict[str, Any]:
        """Query player casualties and enlistments from the official War API.

        If map_name is provided, returns statistics for that specific hex.
        If map_name is omitted, computes global casualties across all 53 active fronts,
        including casualty difference and the top 5 most intense frontlines.

        Args:
            map_name: Optional hex name (e.g. 'DeadLandsHex', 'LinnMercyHex', 'MarbanHollow')
            shard: Shard name (default: 'live-1')
        """
        if map_name:
            report = await self.war_client.get_war_report(map_name, shard=shard)
            if not report:
                return {
                    "error": f"Could not retrieve war report for hex '{map_name}' on shard '{shard}'."
                }
            clock = await self._clock(shard, report_day=report.day_of_war)
            return {
                "shard": shard,
                "map_name": report.map_name,
                "now_ingame": self._label(clock, igt.now()),
                "day_of_war": report.day_of_war,
                "total_enlistments": report.total_enlistments,
                "colonial_casualties": report.colonial_casualties,
                "warden_casualties": report.warden_casualties,
                "total_casualties": report.total_casualties,
                "casualty_difference": report.warden_casualties - report.colonial_casualties,
            }

        global_stats = await self.war_client.get_global_casualties(shard=shard)
        if not global_stats:
            return {"error": f"Could not compute global casualties for shard '{shard}."}

        return global_stats.model_dump()

    @staticmethod
    def _label(state: igt.ClockState | None, t: float) -> str | None:
        clock = igt.to_ingame(state, t) if state else None
        if clock is None:
            return None
        if clock.calibrated and clock.plus_minus_minutes <= 3:
            return clock.label
        return f"{clock.label} (±{clock.plus_minus_minutes:g} min)"

    async def get_active_maps(self, shard: str = DEFAULT_SHARD) -> dict[str, Any]:
        """List all active World Conquest map hexes on the server.

        Args:
            shard: Shard name (default: 'live-1')
        """
        maps = await self.war_client.get_maps(shard=shard)
        return {"shard": shard, "total_maps": len(maps), "maps": maps}

    async def get_map_intel(
        self,
        map_name: str,
        filter_category: str | None = None,
        shard: str = DEFAULT_SHARD,
    ) -> dict[str, Any]:
        """Retrieve tactical intelligence for a map hex: base control, victory points, facilities, and resource fields.

        Args:
            map_name: Hex name (e.g. 'DeadLandsHex', 'MarbanHollow', 'WestgateHex')
            filter_category: Optional filter: 'bases', 'logistics', 'resources', 'defenses', 'rockets', or 'aircraft'
            shard: Shard name (default: 'live-1')
        """
        dynamic_data = await self.war_client.get_dynamic_map_data(map_name, shard=shard)
        static_data = await self.war_client.get_static_map_data(map_name, shard=shard)

        if not dynamic_data and not static_data:
            return {
                "error": f"Could not retrieve map telemetry for '{map_name}' on shard '{shard}."
            }

        # Combine items
        items = []
        if dynamic_data and dynamic_data.map_items:
            items.extend(dynamic_data.map_items)
        if static_data and static_data.map_items:
            # Add static items that aren't already represented
            existing_coords = {(round(i.x, 4), round(i.y, 4)) for i in items}
            for s_item in static_data.map_items:
                if (round(s_item.x, 4), round(s_item.y, 4)) not in existing_coords:
                    items.append(s_item)

        # Apply category filter if specified
        if filter_category:
            cat_lower = filter_category.lower().strip()
            allowed_icons = ICON_CATEGORIES.get(cat_lower)
            if allowed_icons:
                items = [i for i in items if i.icon_type in allowed_icons]

        # Summarize base control
        warden_bases = [
            i.icon_name for i in items if i.team_id == "WARDENS" and "Base" in i.icon_name
        ]
        colonial_bases = [
            i.icon_name for i in items if i.team_id == "COLONIALS" and "Base" in i.icon_name
        ]
        victory_points = [
            {"name": i.icon_name, "team": i.team_id, "scorched": i.is_scorched}
            for i in items
            if i.is_victory_base
        ]

        major_locations = []
        if static_data and static_data.map_text_items:
            major_locations = [
                t.text for t in static_data.map_text_items if t.map_marker_type == "Major"
            ]

        out = {
            "shard": shard,
            "map_name": map_name,
            "total_tracked_items": len(items),
            "victory_points": victory_points,
            "warden_controlled_bases": len(warden_bases),
            "colonial_controlled_bases": len(colonial_bases),
            "major_locations": major_locations,
            "items": [
                {
                    "icon": i.icon_name,
                    "team": i.team_id,
                    "x": round(i.x, 4),
                    "y": round(i.y, 4),
                    "is_victory_base": i.is_victory_base,
                    "is_scorched": i.is_scorched,
                    "is_build_site": i.is_build_site,
                }
                for i in items
            ],
        }
        return out

    async def get_victory_town_status(self, shard: str = DEFAULT_SHARD) -> dict[str, Any]:
        """Calculate the global victory town score and victory requirement for the active war.

        Args:
            shard: Shard name (default: 'live-1')
        """
        vt_status = await self.war_client.get_victory_town_status(shard=shard)
        if not vt_status:
            return {"error": f"Failed to calculate victory town status for shard '{shard}."}
        return vt_status.model_dump()


# Default singleton instance for convenience
default_war_tools = WarApiTools()


def __getattr__(name: str):
    """PEP 562: delegate attribute access to the default singleton instance."""
    return getattr(default_war_tools, name)


def __dir__():
    """PEP 562: return dir of the default singleton instance merged with module attributes."""
    attrs = set(globals().keys())
    attrs.update(dir(default_war_tools))
    return sorted(attrs)
