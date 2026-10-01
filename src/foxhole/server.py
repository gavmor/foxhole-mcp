"""MCP Server implementation exposing structured Foxhole game tools and War API."""

import json
import logging

from mcp.server.mcpserver import MCPServer

from foxhole.client import FoxholeWikiClient
from foxhole.leontief import (
    LeontiefRequest,
    MachineSpec,
)
from foxhole.leontief import (
    solve_leontief as calculate_leontief,
)
from foxhole.parser import (
    parse_item,
    parse_page_content,
    parse_structure,
    parse_vehicle,
)
from foxhole.warapi import (
    DEFAULT_SHARD,
    ICON_CATEGORIES,
    WarApiClient,
)

logger = logging.getLogger(__name__)

# Initialize MCPServer instance
server = MCPServer(
    name="foxhole",
    description="Foxhole MCP server combining MediaWiki data with live War API telemetry",
    version="0.2.0",
)

client = FoxholeWikiClient()
war_client = WarApiClient()


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


# ---------------------------------------------------------------------------
# MediaWiki & Game Data Tools
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# War API Telemetry Tools (Live Game World Conquest)
# ---------------------------------------------------------------------------


@server.tool()
async def get_war_status(shard: str = DEFAULT_SHARD) -> str:
    """Query live World Conquest status, war number, active winner, and victory requirements.

    Args:
        shard: Target shard: 'live-1' (Able), 'live-2' (Baker), 'live-3' (Charlie), or 'dev'
    """
    state = await war_client.get_war_state(shard=shard)
    if not state:
        return json.dumps(
            {"error": f"Failed to retrieve War state from shard '{shard}'. Server may be offline."}
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


@server.tool()
async def get_war_casualties(map_name: str | None = None, shard: str = DEFAULT_SHARD) -> str:
    """Query player casualties and enlistments from the official War API.

    If map_name is provided, returns statistics for that specific hex.
    If map_name is omitted, computes global casualties across all 53 active fronts,
    including casualty difference and the top 5 most intense frontlines.

    Args:
        map_name: Optional hex name (e.g. 'DeadLandsHex', 'LinnMercyHex', 'MarbanHollow')
        shard: Shard name (default: 'live-1')
    """
    if map_name:
        report = await war_client.get_war_report(map_name, shard=shard)
        if not report:
            return json.dumps(
                {"error": f"Could not retrieve war report for hex '{map_name}' on shard '{shard}'."}
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

    global_stats = await war_client.get_global_casualties(shard=shard)
    if not global_stats:
        return json.dumps({"error": f"Could not compute global casualties for shard '{shard}'."})

    return json.dumps(global_stats.model_dump(), indent=2)


@server.tool()
async def get_active_maps(shard: str = DEFAULT_SHARD) -> str:
    """List all active World Conquest map hexes on the server.

    Args:
        shard: Shard name (default: 'live-1')
    """
    maps = await war_client.get_maps(shard=shard)
    return json.dumps({"shard": shard, "total_maps": len(maps), "maps": maps}, indent=2)


@server.tool()
async def get_map_intel(
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
    dynamic_data = await war_client.get_dynamic_map_data(map_name, shard=shard)
    static_data = await war_client.get_static_map_data(map_name, shard=shard)

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
    warden_bases = [i.icon_name for i in items if i.team_id == "WARDENS" and "Base" in i.icon_name]
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


@server.tool()
async def get_victory_town_status(shard: str = DEFAULT_SHARD) -> str:
    """Calculate the global victory town score and victory requirement for the active war.

    Args:
        shard: Shard name (default: 'live-1')
    """
    vt_status = await war_client.get_victory_town_status(shard=shard)
    if not vt_status:
        return json.dumps(
            {"error": f"Failed to calculate victory town status for shard '{shard}'."}
        )
    return json.dumps(vt_status.model_dump(), indent=2)


# ---------------------------------------------------------------------------
# Leontief Input-Output Factory Optimization
# ---------------------------------------------------------------------------


@server.tool()
def solve_leontief(
    items: list[str],
    coefficients_matrix: list[list[float]],
    external_demand: dict[str, float],
    machines: dict[str, MachineSpec] | None = None,
) -> str:
    """Solve the Leontief balance equation (I - A)x = d for gross production rates and machine counts.

    Solves the linear input-output economic model using NumPy (np.linalg.solve(I - A, d)).
    Computes gross production rates (x) required to satisfy target net output (d) while
    accounting for internal recipe consumption loops (c = Ax). Optionally calculates
    exact fractional and integer machine counts (N = x*t / (y*s)).

    Guards against:
    - Matrix dimension mismatches
    - Missing demand items
    - Singular loops (LinAlgError)
    - Hawkins-Simon condition violations (negative production indicating impossible loops)

    Args:
        items: Ordered list of item names, e.g. ['circuit', 'wire', 'plate']
        coefficients_matrix: Matrix A where A[i][j] is unit amount of item i needed to produce 1 unit of item j
        external_demand: Desired net export rate per second {item: demand_rate}
        machines: Optional machine specs per item {item: {crafting_time, yield_per_craft, machine_speed}}
    """
    try:
        req = LeontiefRequest(
            items=items,
            coefficients_matrix=coefficients_matrix,
            external_demand=external_demand,
            machines=machines,
        )
        result = calculate_leontief(req)
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


# ---------------------------------------------------------------------------
# MCP Prompts
# ---------------------------------------------------------------------------


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


@server.prompt()
def strategic_war_overview(shard: str = DEFAULT_SHARD) -> str:
    """Prompt template for synthesizing live war situation reports."""
    return f"""Please generate a high-level strategic intelligence briefing on Foxhole Shard '{shard}':
1. Check current War number, status (Active vs Resistance Phase), and victor if concluded.
2. Review global casualty totals, casualty ratio between Wardens and Colonials, and identify the most contested fronts.
3. Check Victory Town control and proximity to conquest victory condition.
4. Highlight major logistical bottlenecks or tactical opportunities for the front lines."""


@server.prompt()
def frontline_intel(map_name: str, shard: str = DEFAULT_SHARD) -> str:
    """Prompt template for detailed sector intelligence on a specific hex."""
    return f"""Please provide a tactical sector report for hex '{map_name}' on Foxhole Shard '{shard}':
1. Fetch latest casualties and enlistments to gauge front-line intensity.
2. Review base control distribution between Colonials and Wardens.
3. Identify presence of Victory Towns, scorched bases, or rocket targets.
4. Map key logistics assets (factories, refineries, seaports) and strategic approach angles."""


@server.prompt()
def leontief_facility_planner(target_production: str) -> str:
    """Prompt template for formulating and solving a multi-tier facility supply chain."""
    return f"""Please formulate and solve the Leontief input-output balance equation for this facility goal:
Target: {target_production}

Steps:
1. Identify all raw resources, intermediate components, and final products in the supply chain.
2. Build the ordered list of items: items = [item_1, item_2, ...]
3. Construct the technical coefficients matrix A where A[i][j] is the units of item i consumed to produce 1 unit of item j.
4. Define the external net demand vector d.
5. If machine cycle times are known, define machine specifications (crafting_time, yield_per_craft, machine_speed).
6. Call `solve_leontief` with (items, coefficients_matrix, external_demand, machines) to compute gross rates, internal consumption, and exact facility counts.
7. Interpret the results and check for any logistical bottlenecks."""
