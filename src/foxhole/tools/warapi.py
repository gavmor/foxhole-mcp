"""MCP tools for querying Foxhole live War API telemetry and world state."""

import json
import logging

from mcp.server.mcpserver import MCPServer

from foxhole.warapi import (
    DEFAULT_SHARD,
    ICON_CATEGORIES,
    WarApiClient,
)

logger = logging.getLogger(__name__)


class WarApiTools:
    """Encapsulates War API tools and their associated client."""

    def __init__(self, war_client: WarApiClient | None = None) -> None:
        self.war_client = war_client or WarApiClient()

    async def close(self) -> None:
        """Close the underlying War API client."""
        await self.war_client.close()

    async def get_war_status(self, shard: str = DEFAULT_SHARD) -> str:
        """Query live World Conquest status, war number, active winner, and victory requirements.

        Args:
            shard: Target shard: 'live-1' (Able), 'live-2' (Baker), 'live-3' (Charlie), or 'dev'
        """
        state = await self.war_client.get_war_state(shard=shard)
        if not state:
            return json.dumps(
                {
                    "error": f"Failed to retrieve War state from shard '{shard}'. Server may be offline."
                }
            )

        out = {
            "shard": shard,
            "war_number": state.war_number,
            "war_id": state.war_id,
            "status": state.status_display,
            "is_active": state.is_active,
            "winner": state.winner,
            "start_time_utc": state.start_datetime,
            "required_victory_towns": state.required_victory_towns,
            "short_required_victory_towns": state.short_required_victory_towns,
        }
        return json.dumps(out, indent=2)

    async def get_war_casualties(
        self, map_name: str | None = None, shard: str = DEFAULT_SHARD
    ) -> str:
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
                return json.dumps(
                    {
                        "error": (
                            f"Could not retrieve war report for hex '{map_name}' on shard '{shard}'."
                        )
                    }
                )
            return json.dumps(
                {
                    "shard": shard,
                    "map_name": report.map_name,
                    "day_of_war": report.day_of_war,
                    "total_enlistments": report.total_enlistments,
                    "colonial_casualties": report.colonial_casualties,
                    "warden_casualties": report.warden_casualties,
                    "total_casualties": report.total_casualties,
                    "casualty_difference": report.warden_casualties - report.colonial_casualties,
                },
                indent=2,
            )

        global_stats = await self.war_client.get_global_casualties(shard=shard)
        if not global_stats:
            return json.dumps(
                {"error": f"Could not compute global casualties for shard '{shard}'."}
            )

        return json.dumps(global_stats.model_dump(), indent=2)

    async def get_active_maps(self, shard: str = DEFAULT_SHARD) -> str:
        """List all active World Conquest map hexes on the server.

        Args:
            shard: Shard name (default: 'live-1')
        """
        maps = await self.war_client.get_maps(shard=shard)
        return json.dumps({"shard": shard, "total_maps": len(maps), "maps": maps}, indent=2)

    async def get_map_intel(
        self,
        map_name: str,
        filter_category: str | None = None,
        shard: str = DEFAULT_SHARD,
    ) -> str:
        """Retrieve tactical intelligence for a map hex: base control, victory points, facilities, and resource fields.

        Args:
            map_name: Hex name (e.g. 'DeadLandsHex', 'MarbanHollow', 'WestgateHex')
            filter_category: Optional filter: 'bases', 'logistics', 'resources', 'defenses', 'rockets', or 'aircraft'
            shard: Shard name (default: 'live-1')
        """
        dynamic_data = await self.war_client.get_dynamic_map_data(map_name, shard=shard)
        static_data = await self.war_client.get_static_map_data(map_name, shard=shard)

        if not dynamic_data and not static_data:
            return json.dumps(
                {"error": f"Could not retrieve map telemetry for '{map_name}' on shard '{shard}'."}
            )

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
        return json.dumps(out, indent=2)

    async def get_victory_town_status(self, shard: str = DEFAULT_SHARD) -> str:
        """Calculate the global victory town score and victory requirement for the active war.

        Args:
            shard: Shard name (default: 'live-1')
        """
        vt_status = await self.war_client.get_victory_town_status(shard=shard)
        if not vt_status:
            return json.dumps(
                {"error": f"Failed to calculate victory town status for shard '{shard}'."}
            )
        return json.dumps(vt_status.model_dump(), indent=2)

    def register(self, server: MCPServer) -> None:
        """Register all War API tools with the given MCP server."""
        server.add_tool(self.get_war_status)
        server.add_tool(self.get_war_casualties)
        server.add_tool(self.get_active_maps)
        server.add_tool(self.get_map_intel)
        server.add_tool(self.get_victory_town_status)


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
