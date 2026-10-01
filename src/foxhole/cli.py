"""Command-line interface for Foxhole MCP Server, MediaWiki tools, and War API."""

import argparse
import asyncio
import json
import sys

from foxhole.client import FoxholeWikiClient
from foxhole.parser import parse_item, parse_page_content, parse_structure, parse_vehicle
from foxhole.server import _resolve_title, server
from foxhole.warapi import DEFAULT_SHARD, WarApiClient


def _format_dict(d: dict) -> str:
    return json.dumps(d, indent=2)


# ---------------------------------------------------------------------------
# Wiki CLI Actions
# ---------------------------------------------------------------------------


async def run_search(query: str, limit: int = 5) -> None:
    client = FoxholeWikiClient()
    try:
        results = await client.search(query, limit=limit)
        if not results:
            print(f"No results found for '{query}'.")
            return
        print(f"\n--- Search results for '{query}' ---")
        for i, r in enumerate(results, 1):
            print(f"\n{i}. {r.title} ({r.wordcount} words)")
            print(f"   URL: {r.url}")
            if r.snippet:
                print(f"   {r.snippet}")
    finally:
        await client.close()


async def run_vehicle(name: str) -> None:
    client = FoxholeWikiClient()
    try:
        resolved = await _resolve_title(name)
        data = await client.get_page_data(resolved)
        if not data or not data.get("wikitext"):
            print(f"Could not find article for '{name}'.")
            return
        v = parse_vehicle(data["title"], data["wikitext"])
        if not v:
            print(f"'{data['title']}' is not categorized as a vehicle.")
            return
        print(_format_dict(v.model_dump(exclude_none=True)))
    finally:
        await client.close()


async def run_item(name: str) -> None:
    client = FoxholeWikiClient()
    try:
        resolved = await _resolve_title(name)
        data = await client.get_page_data(resolved)
        if not data or not data.get("wikitext"):
            print(f"Could not find article for '{name}'.")
            return
        item = parse_item(data["title"], data["wikitext"])
        if not item:
            print(f"'{data['title']}' is not categorized as an item.")
            return
        print(_format_dict(item.model_dump(exclude_none=True)))
    finally:
        await client.close()


async def run_structure(name: str) -> None:
    client = FoxholeWikiClient()
    try:
        resolved = await _resolve_title(name)
        data = await client.get_page_data(resolved)
        if not data or not data.get("wikitext"):
            print(f"Could not find article for '{name}'.")
            return
        s = parse_structure(data["title"], data["wikitext"])
        if not s:
            print(f"'{data['title']}' is not categorized as a structure.")
            return
        print(_format_dict(s.model_dump(exclude_none=True)))
    finally:
        await client.close()


async def run_page(title: str) -> None:
    client = FoxholeWikiClient()
    try:
        data = await client.get_page_data(title)
        if not data or not data.get("wikitext"):
            print(f"Could not find page '{title}'.")
            return
        page = parse_page_content(data["title"], data["wikitext"])
        print(_format_dict(page.model_dump(exclude_none=True)))
    finally:
        await client.close()


async def run_edit(
    title: str,
    content: str,
    summary: str = "Edited via Foxhole CLI",
    section: str | None = None,
    minor: bool = False,
    bot: bool = False,
) -> None:
    from foxhole.tools.wiki import default_wiki_tools

    try:
        res = await default_wiki_tools.edit_wiki_page(
            title=title,
            content=content,
            summary=summary,
            section=section,
            minor=minor,
            bot=bot,
        )
        print(res)
    finally:
        await default_wiki_tools.close()


# ---------------------------------------------------------------------------
# War API CLI Actions
# ---------------------------------------------------------------------------


async def run_war_status(shard: str = DEFAULT_SHARD) -> None:
    war_client = WarApiClient()
    try:
        state = await war_client.get_war_state(shard=shard)
        if not state:
            print(f"Failed to fetch war state from shard '{shard}'.")
            return
        print(
            _format_dict(
                {
                    "shard": shard,
                    "war_number": state.war_number,
                    "status": state.status_display,
                    "winner": state.winner,
                    "started_at": state.start_datetime,
                    "required_victory_towns": state.required_victory_towns,
                }
            )
        )
    finally:
        await war_client.close()


async def run_casualties(map_name: str | None = None, shard: str = DEFAULT_SHARD) -> None:
    war_client = WarApiClient()
    try:
        if map_name:
            report = await war_client.get_war_report(map_name, shard=shard)
            if not report:
                print(f"Could not find report for map '{map_name}'.")
                return
            print(_format_dict(report.model_dump()))
        else:
            print(f"Aggregating casualties across all active fronts on '{shard}'...")
            global_stats = await war_client.get_global_casualties(shard=shard)
            if not global_stats:
                print(f"Could not compute global casualties for '{shard}'.")
                return
            print(_format_dict(global_stats.model_dump()))
    finally:
        await war_client.close()


async def run_maps(shard: str = DEFAULT_SHARD) -> None:
    war_client = WarApiClient()
    try:
        maps = await war_client.get_maps(shard=shard)
        print(f"--- Active Maps on '{shard}' ({len(maps)} total) ---")
        for m in sorted(maps):
            print(f"  • {m}")
    finally:
        await war_client.close()


async def run_intel(
    map_name: str,
    category: str | None = None,
    shard: str = DEFAULT_SHARD,
) -> None:
    war_client = WarApiClient()
    try:
        dyn = await war_client.get_dynamic_map_data(map_name, shard=shard)
        st = await war_client.get_static_map_data(map_name, shard=shard)
        if not dyn and not st:
            print(f"Could not retrieve telemetry for map '{map_name}'.")
            return

        items = dyn.map_items if dyn else []
        print(f"\n--- Tactical Intel: {map_name} ({shard}) ---")
        print(f"Tracked dynamic items: {len(items)}")

        vp_items = [i for i in items if i.is_victory_base]
        if vp_items:
            print("\nVictory Towns:")
            for vp in vp_items:
                status = "SCORCHED" if vp.is_scorched else vp.team_id
                print(f"  ★ {vp.icon_name} -> {status}")

        major_locs = (
            [t.text for t in st.map_text_items if t.map_marker_type == "Major"] if st else []
        )
        if major_locs:
            print("\nMajor Locations:")
            print(f"  {', '.join(major_locs)}")
    finally:
        await war_client.close()


async def run_victory(shard: str = DEFAULT_SHARD) -> None:
    war_client = WarApiClient()
    try:
        print(f"Calculating victory town control across all maps for '{shard}'...")
        vt_status = await war_client.get_victory_town_status(shard=shard)
        if not vt_status:
            print(f"Failed to calculate victory status for shard '{shard}'.")
            return
        print(_format_dict(vt_status.model_dump()))
    finally:
        await war_client.close()


async def run_plan(
    target: str,
    quantity: float = 1.0,
    overrides: str | None = None,
    choice: str | None = None,
) -> None:
    from foxhole.server import client, plan_production

    try:
        print(
            await plan_production(
                target,
                quantity,
                recipe_overrides=json.loads(overrides) if overrides else None,
                recipe_choice=json.loads(choice) if choice else None,
            )
        )
    finally:
        await client.close()


def run_resources(
    item_or_vehicle: str,
    quantity: float = 1.0,
    machines: bool = False,
    time_window: float | None = None,
) -> None:
    from foxhole.economy import get_economy_solver

    solver = get_economy_solver()
    try:
        plan = solver.solve(
            demand={item_or_vehicle: quantity},
            include_machine_counts=machines,
            time_window_seconds=time_window,
        )
        print(_format_dict(plan.model_dump(exclude_none=True)))
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main CLI Dispatcher
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="foxhole",
        description="Foxhole MediaWiki MCP Server & War API telemetry tools",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # MCP server command
    mcp_parser = subparsers.add_parser("mcp", help="Run the Foxhole MCP server")
    mcp_parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport type for MCP (default: stdio)",
    )
    mcp_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for SSE transport (default: 8000)",
    )

    # Wiki Commands
    search_parser = subparsers.add_parser("search", help="Search the Foxhole wiki")
    search_parser.add_argument("query", help="Search query string")
    search_parser.add_argument("-n", "--limit", type=int, default=5, help="Number of results")

    veh_parser = subparsers.add_parser("vehicle", help="Look up vehicle specifications")
    veh_parser.add_argument("name", help="Vehicle name or variant")

    item_parser = subparsers.add_parser("item", help="Look up item or weapon specifications")
    item_parser.add_argument("name", help="Item name")

    struct_parser = subparsers.add_parser("structure", help="Look up structure specifications")
    struct_parser.add_argument("name", help="Structure name")

    page_parser = subparsers.add_parser("page", help="Get clean text and overview of a wiki page")
    page_parser.add_argument("title", help="Page title")

    edit_parser = subparsers.add_parser(
        "edit", help="Create or edit a wiki page on foxhole.wiki.gg"
    )
    edit_parser.add_argument("title", help="Page title")
    edit_parser.add_argument("content", help="Wikitext content to write")
    edit_parser.add_argument(
        "-s", "--summary", default="Edited via Foxhole CLI", help="Edit summary"
    )
    edit_parser.add_argument(
        "--section", default=None, help="Section name or number ('new' to append)"
    )
    edit_parser.add_argument("--minor", action="store_true", help="Mark edit as minor")
    edit_parser.add_argument("--bot", action="store_true", help="Mark edit as bot")

    # War API Commands
    war_parser = subparsers.add_parser("war", help="Get current World Conquest status")
    war_parser.add_argument("--shard", default=DEFAULT_SHARD, help="Target shard (default: live-1)")

    cas_parser = subparsers.add_parser("casualties", help="Get casualty reports")
    cas_parser.add_argument("-m", "--map", dest="map_name", help="Hex map name (default: all)")
    cas_parser.add_argument("--shard", default=DEFAULT_SHARD, help="Target shard (default: live-1)")

    maps_parser = subparsers.add_parser("maps", help="List active World Conquest hexes")
    maps_parser.add_argument(
        "--shard", default=DEFAULT_SHARD, help="Target shard (default: live-1)"
    )

    intel_parser = subparsers.add_parser("intel", help="Get tactical map telemetry")
    intel_parser.add_argument("map_name", help="Hex map name")
    intel_parser.add_argument("-c", "--category", help="Category filter")
    intel_parser.add_argument(
        "--shard", default=DEFAULT_SHARD, help="Target shard (default: live-1)"
    )

    vic_parser = subparsers.add_parser("victory", help="Get victory town scores and requirements")
    vic_parser.add_argument("--shard", default=DEFAULT_SHARD, help="Target shard (default: live-1)")

    # Production Planner (BOM rollup over wiki recipes)
    plan_parser = subparsers.add_parser(
        "plan", help="Plan production: full bill of materials down to raw resources"
    )
    plan_parser.add_argument("target", help="Item, vehicle, or structure name")
    plan_parser.add_argument(
        "-q", "--quantity", type=float, default=1.0, help="Units to produce (default: 1)"
    )
    plan_parser.add_argument(
        "--overrides", help="JSON recipe overrides, e.g. '{\"Basic Materials\": {}}'"
    )
    plan_parser.add_argument(
        "--choice", help="JSON alternative recipe indices, e.g. '{\"Construction Materials\": 1}'"
    )

    # Curried Leontief Resources & BOM Calculator
    res_parser = subparsers.add_parser(
        "resources",
        aliases=["bom", "calc"],
        help="Calculate total required raw resources and BOM using curried Leontief matrix",
    )
    res_parser.add_argument(
        "item",
        help="Name or alias of item/vehicle (e.g. '00MS “Stinger”', 'bike-mounted machine gun', 'Spatha')",
    )
    res_parser.add_argument(
        "-q", "--quantity", type=float, default=1.0, help="Target quantity (default: 1.0)"
    )
    res_parser.add_argument(
        "-m",
        "--machines",
        action="store_true",
        help="Calculate required facility buildings/stations",
    )
    res_parser.add_argument(
        "-t", "--time", type=float, default=3600.0, help="Time budget in seconds (default: 3600s)"
    )

    args = parser.parse_args()

    if args.command is None or args.command == "mcp":
        transport = getattr(args, "transport", "stdio")
        if transport == "sse":
            server.run(transport="sse", port=getattr(args, "port", 8000))
        else:
            server.run(transport="stdio")
    elif args.command == "search":
        asyncio.run(run_search(args.query, args.limit))
    elif args.command == "vehicle":
        asyncio.run(run_vehicle(args.name))
    elif args.command == "item":
        asyncio.run(run_item(args.name))
    elif args.command == "structure":
        asyncio.run(run_structure(args.name))
    elif args.command == "page":
        asyncio.run(run_page(args.title))
    elif args.command == "edit":
        asyncio.run(
            run_edit(
                title=args.title,
                content=args.content,
                summary=args.summary,
                section=args.section,
                minor=args.minor,
                bot=args.bot,
            )
        )
    elif args.command == "war":
        asyncio.run(run_war_status(args.shard))
    elif args.command == "casualties":
        asyncio.run(run_casualties(args.map_name, args.shard))
    elif args.command == "maps":
        asyncio.run(run_maps(args.shard))
    elif args.command == "intel":
        asyncio.run(run_intel(args.map_name, args.category, args.shard))
    elif args.command == "victory":
        asyncio.run(run_victory(args.shard))
    elif args.command in ("resources", "bom", "calc"):
        run_resources(
            item_or_vehicle=args.item,
            quantity=args.quantity,
            machines=args.machines,
            time_window=args.time,
        )
    elif args.command == "plan":
        asyncio.run(run_plan(args.target, args.quantity, args.overrides, args.choice))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
