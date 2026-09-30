"""MCP Server implementation exposing structured Foxhole game tools."""

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

# Initialize MCPServer instance
server = MCPServer(
    name="foxhole",
    description="Official MediaWiki MCP server for Foxhole with structured data parsing",
    version="0.1.0",
)

client = FoxholeWikiClient()


async def _resolve_title(name: str) -> str:
    """Resolve aliases or nicknames using opensearch suggestions if needed."""
    # Check exact match first
    data = await client.get_page_data(name)
    if data and data.get("wikitext"):
        # If it's not a disambiguation page, return as is
        if "{{disambig" not in data["wikitext"].lower():
            return data["title"]

    # Try opensearch for close matches
    suggestions = await client.opensearch(name, limit=5)
    if suggestions:
        # Prefer non-disambiguation matches
        for sug in suggestions:
            sug_data = await client.get_page_data(sug)
            if (
                sug_data
                and sug_data.get("wikitext")
                and "{{disambig" not in sug_data["wikitext"].lower()
            ):
                return sug_data["title"]
        return suggestions[0]

    return name


@server.tool()
async def search_foxhole_wiki(query: str, limit: int = 5) -> str:
    """Search the Foxhole wiki (foxhole.wiki.gg) for articles matching the query.

    Args:
        query: Search term (e.g. 'Storm Cannon', 'Devitt', 'Heavy Explosive')
        limit: Maximum number of search results to return (default: 5)
    """
    results = await client.search(query, limit=limit)
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


@server.tool()
async def get_vehicle_stats(vehicle_name: str) -> str:
    """Get structured specifications for any Foxhole vehicle.

    Extracts hit points, armor rating, min/max penetration chances, subsystem
    vulnerabilities (tracks, turret, fuel tank), crew seats, fuel specs, speeds,
    mounted armaments, and production recipes.

    Args:
        vehicle_name: Name or alias of the vehicle (e.g. 'Silverhand - Mk. IV', 'Dunne Transport', 'Falchion')
    """
    resolved = await _resolve_title(vehicle_name)
    data = await client.get_page_data(resolved)
    if not data or not data.get("wikitext"):
        return json.dumps(
            {"error": f"Vehicle '{vehicle_name}' could not be found on foxhole.wiki.gg."}
        )

    vehicle = parse_vehicle(data["title"], data["wikitext"])
    if not vehicle:
        # Fall back to page content if infobox is missing or not a vehicle
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


@server.tool()
async def get_item_stats(item_name: str) -> str:
    """Get structured statistics for weapons, equipment, ammunition, and items.

    Extracts damage, ammo compatibility, magazine size, fire rate, ranges,
    encumbrance, crate packaging sizes, and manufacturing costs.

    Args:
        item_name: Name of weapon, tool, or shell (e.g. 'No.2 Loughcaster', '40mm', 'Gas Mask', 'Bishamon')
    """
    resolved = await _resolve_title(item_name)
    data = await client.get_page_data(resolved)
    if not data or not data.get("wikitext"):
        return json.dumps({"error": f"Item '{item_name}' could not be found on foxhole.wiki.gg."})

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


@server.tool()
async def get_structure_stats(structure_name: str) -> str:
    """Get structured statistics for fortifications, bases, and world buildings.

    Extracts structure HP, armor tier, decay resistance, repair costs,
    mounted guns/artillery, and construction/upgrade recipes.

    Args:
        structure_name: Name of structure (e.g. 'Storm Cannon', 'Bunker Base', 'Rifle Pillbox')
    """
    resolved = await _resolve_title(structure_name)
    data = await client.get_page_data(resolved)
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


@server.tool()
async def get_production_cost(name: str) -> str:
    """Get manufacturing and logistics recipes for any vehicle, weapon, ammo, or structure.

    Returns the production facility (Garage, Factory, MPF, Small Assembly Station),
    material requirements (Basic Materials, Refined Materials, Assembly Materials),
    cycle times, and crate output.

    Args:
        name: Name of entity to query (e.g. 'Dunne Transport', '40mm', 'Silverhand Chieftain - Mk. VI')
    """
    resolved = await _resolve_title(name)
    data = await client.get_page_data(resolved)
    if not data or not data.get("wikitext"):
        return json.dumps({"error": f"Entity '{name}' could not be found on foxhole.wiki.gg."})

    wikitext = data["wikitext"]
    title = data["title"]

    # Check vehicle, item, or structure
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
                "production_recipes": [p.model_dump(exclude_none=True) for p in item.production],
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


@server.tool()
async def get_page_overview(title: str) -> str:
    """Get clean text overview and section contents for any article on foxhole.wiki.gg.

    Removes wikitext syntax, template noise, and navigation boxes.

    Args:
        title: Title of the wiki page (e.g. 'Logistics', 'Artillery', 'Armor Mechanics')
    """
    data = await client.get_page_data(title)
    if not data or not data.get("wikitext"):
        return json.dumps({"error": f"Article '{title}' could not be found."})

    content = parse_page_content(data["title"], data["wikitext"])
    return json.dumps(content.model_dump(exclude_none=True), indent=2)


@server.prompt()
def combat_intel(vehicle_or_weapon: str) -> str:
    """Prompt template for analyzing combat strengths, vulnerabilities, and counter-tactics."""
    return f"""Please provide a comprehensive tactical breakdown of '{vehicle_or_weapon}' in Foxhole:
1. Review its health pool, armor type, and penetration chances.
2. Detail its armament(s), ammo requirements, firing range, and fire rate.
3. List subsystem disable vulnerabilities (e.g., track disable chance).
4. Recommend optimal engagement tactics, counter-vehicles, and logistical support needed."""


@server.prompt()
def logistics_plan(item_name: str, requested_amount: int = 100) -> str:
    """Prompt template for calculating material inputs and shipping crates."""
    return f"""Please create a logistics production and transport plan for {requested_amount} units of '{item_name}':
1. Lookup the crate packaging size and calculate the number of crates needed.
2. Calculate total raw and refined materials required (bmats, rmats, explosive powder, etc.).
3. Determine production facility (Factory vs Mass Production Factory vs Facility) and time required.
4. Provide recommendations for shipping container and truck transport."""
