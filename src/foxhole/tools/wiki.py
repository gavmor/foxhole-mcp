"""MCP tools for querying and parsing Foxhole MediaWiki content."""

import json
import logging

from mcp.server.mcpserver import MCPServer

from foxhole.client import FoxholeWikiClient
from foxhole.parser import (
    parse_item,
    parse_page_content,
    parse_structure,
    parse_vehicle,
)

logger = logging.getLogger(__name__)


class WikiTools:
    """Encapsulates Foxhole MediaWiki tools and their associated client."""

    def __init__(self, client: FoxholeWikiClient | None = None) -> None:
        self.client = client or FoxholeWikiClient()

    async def close(self) -> None:
        """Close the underlying FoxholeWikiClient."""
        await self.client.close()

    async def resolve_title(self, name: str) -> str:
        """Resolve aliases or nicknames using opensearch suggestions if needed."""
        # Check exact match first
        data = await self.client.get_page_data(name)
        if data and data.get("wikitext"):
            # If it's not a disambiguation page, return as is
            if "{{disambig" not in data["wikitext"].lower():
                return data["title"]

        # Try opensearch for close matches
        suggestions = await self.client.opensearch(name, limit=5)
        if suggestions:
            # Prefer non-disambiguation matches
            for sug in suggestions:
                sug_data = await self.client.get_page_data(sug)
                if (
                    sug_data
                    and sug_data.get("wikitext")
                    and "{{disambig" not in sug_data["wikitext"].lower()
                ):
                    return sug_data["title"]
            return suggestions[0]

        return name

    async def search_foxhole_wiki(self, query: str, limit: int = 5) -> str:
        """Search the Foxhole wiki (foxhole.wiki.gg) for articles matching the query.

        Args:
            query: Search term (e.g. 'Storm Cannon', 'Devitt', 'Heavy Explosive')
            limit: Maximum number of search results to return (default: 5)
        """
        results = await self.client.search(query, limit=limit)
        if not results:
            return json.dumps({"query": query, "total_results": 0, "results": []})

        out = [
            {
                "title": r.title,
                "snippet": r.snippet,
                "wordcount": r.wordcount,
                "url": r.url,
            }
            for r in results
        ]
        return json.dumps({"query": query, "total_results": len(out), "results": out}, indent=2)

    async def get_vehicle_stats(self, vehicle_name: str) -> str:
        """Get structured specifications for any Foxhole vehicle.

        Extracts hit points, armor rating, min/max penetration chances, subsystem
        vulnerabilities (tracks, turret, fuel tank), crew seats, fuel specs, speeds,
        mounted armaments, and production recipes.

        Args:
            vehicle_name: Name or alias of the vehicle (e.g. 'Silverhand - Mk. IV', 'Dunne Transport', 'Falchion')
        """
        resolved = await self.resolve_title(vehicle_name)
        data = await self.client.get_page_data(resolved)
        if not data or not data.get("wikitext"):
            return json.dumps(
                {"error": f"Vehicle '{vehicle_name}' could not be found on foxhole.wiki.gg."}
            )

        vehicle = parse_vehicle(data["title"], data["wikitext"])
        if not vehicle:
            content = parse_page_content(data["title"], data["wikitext"])
            return json.dumps(
                {
                    "warning": f"'{data['title']}' is not categorized with a Vehicle Infobox.",
                    "title": content.title,
                    "summary": content.summary,
                    "url": content.wiki_url,
                },
                indent=2,
            )

        return json.dumps(vehicle.model_dump(exclude_none=True), indent=2)

    async def get_item_stats(self, item_name: str) -> str:
        """Get structured statistics for weapons, equipment, ammunition, and items.

        Extracts damage, ammo compatibility, magazine size, fire rate, ranges,
        encumbrance, crate packaging sizes, and manufacturing costs.

        Args:
            item_name: Name of weapon, tool, or shell (e.g. 'No.2 Loughcaster', '40mm', 'Gas Mask', 'Bishamon')
        """
        resolved = await self.resolve_title(item_name)
        data = await self.client.get_page_data(resolved)
        if not data or not data.get("wikitext"):
            return json.dumps(
                {"error": f"Item '{item_name}' could not be found on foxhole.wiki.gg."}
            )

        item = parse_item(data["title"], data["wikitext"])
        if not item:
            content = parse_page_content(data["title"], data["wikitext"])
            return json.dumps(
                {
                    "warning": f"'{data['title']}' is not categorized with an Item Infobox.",
                    "title": content.title,
                    "summary": content.summary,
                    "url": content.wiki_url,
                },
                indent=2,
            )

        return json.dumps(item.model_dump(exclude_none=True), indent=2)

    async def get_structure_stats(self, structure_name: str) -> str:
        """Get structured statistics for fortifications, bases, and world buildings.

        Extracts structure HP, armor tier, decay resistance, repair costs,
        mounted guns/artillery, and construction/upgrade recipes.

        Args:
            structure_name: Name of structure (e.g. 'Storm Cannon', 'Bunker Base', 'Rifle Pillbox')
        """
        resolved = await self.resolve_title(structure_name)
        data = await self.client.get_page_data(resolved)
        if not data or not data.get("wikitext"):
            return json.dumps(
                {"error": f"Structure '{structure_name}' could not be found on foxhole.wiki.gg."}
            )

        structure = parse_structure(data["title"], data["wikitext"])
        if not structure:
            content = parse_page_content(data["title"], data["wikitext"])
            return json.dumps(
                {
                    "warning": f"'{data['title']}' is not categorized with a Structure Infobox.",
                    "title": content.title,
                    "summary": content.summary,
                    "url": content.wiki_url,
                },
                indent=2,
            )

        return json.dumps(structure.model_dump(exclude_none=True), indent=2)

    async def get_production_cost(self, name: str) -> str:
        """Get manufacturing and logistics recipes for any vehicle, weapon, ammo, or structure.

        Returns the production facility (Garage, Factory, MPF, Small Assembly Station),
        material requirements (Basic Materials, Refined Materials, Assembly Materials),
        cycle times, and crate output.

        Args:
            name: Name of entity to query (e.g. 'Dunne Transport', '40mm', 'Silverhand Chieftain - Mk. VI')
        """
        resolved = await self.resolve_title(name)
        data = await self.client.get_page_data(resolved)
        if not data or not data.get("wikitext"):
            return json.dumps({"error": f"Entity '{name}' could not be found on foxhole.wiki.gg."})

        wikitext = data["wikitext"]
        title = data["title"]

        v = parse_vehicle(title, wikitext)
        if v and v.production:
            return json.dumps(
                {
                    "name": v.name,
                    "entity_type": "vehicle",
                    "production_recipes": [p.model_dump(exclude_none=True) for p in v.production],
                    "wiki_url": v.wiki_url,
                },
                indent=2,
            )

        item = parse_item(title, wikitext)
        if item and item.production:
            return json.dumps(
                {
                    "name": item.name,
                    "entity_type": "item",
                    "crate_amount": item.crate_amount,
                    "production_recipes": [
                        p.model_dump(exclude_none=True) for p in item.production
                    ],
                    "wiki_url": item.wiki_url,
                },
                indent=2,
            )

        s = parse_structure(title, wikitext)
        if s and s.production:
            return json.dumps(
                {
                    "name": s.name,
                    "entity_type": "structure",
                    "production_recipes": [p.model_dump(exclude_none=True) for p in s.production],
                    "wiki_url": s.wiki_url,
                },
                indent=2,
            )

        return json.dumps(
            {
                "name": title,
                "message": "No standard production recipes were parsed from this article's infobox.",
                "wiki_url": f"https://foxhole.wiki.gg/wiki/{title.replace(' ', '_')}",
            },
            indent=2,
        )

    async def get_page_overview(self, title: str) -> str:
        """Get clean text overview and section contents for any article on foxhole.wiki.gg.

        Removes wikitext syntax, template noise, and navigation boxes.

        Args:
            title: Title of the wiki page (e.g. 'Logistics', 'Artillery', 'Armor Mechanics')
        """
        data = await self.client.get_page_data(title)
        if not data or not data.get("wikitext"):
            return json.dumps({"error": f"Article '{title}' could not be found."})

        content = parse_page_content(data["title"], data["wikitext"])
        return json.dumps(content.model_dump(exclude_none=True), indent=2)

    def register(self, server: MCPServer) -> None:
        """Register all wiki tools with the given MCP server."""
        server.add_tool(self.search_foxhole_wiki)
        server.add_tool(self.get_vehicle_stats)
        server.add_tool(self.get_item_stats)
        server.add_tool(self.get_structure_stats)
        server.add_tool(self.get_production_cost)
        server.add_tool(self.get_page_overview)


# Default singleton instance for convenience
default_wiki_tools = WikiTools()

# Module-level tool callables delegating to default instance
_resolve_title = default_wiki_tools.resolve_title
resolve_title = default_wiki_tools.resolve_title
search_foxhole_wiki = default_wiki_tools.search_foxhole_wiki
get_vehicle_stats = default_wiki_tools.get_vehicle_stats
get_item_stats = default_wiki_tools.get_item_stats
get_structure_stats = default_wiki_tools.get_structure_stats
get_production_cost = default_wiki_tools.get_production_cost
get_page_overview = default_wiki_tools.get_page_overview
