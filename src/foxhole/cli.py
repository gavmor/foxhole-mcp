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


def run_leontief(
    json_input: str | None = None,
    file_path: str | None = None,
    demo: bool = False,
) -> None:
    from foxhole.leontief import LeontiefRequest, MachineSpec, solve_leontief

    if demo:
        req = LeontiefRequest(
            items=["circuit", "wire", "plate"],
            coefficients_matrix=[
                [0.0, 0.0, 0.0],
                [3.0, 0.0, 0.1],
                [1.0, 0.0, 0.0],
            ],
            external_demand={"circuit": 10.0, "wire": 0.0, "plate": 5.0},
            machines={
                "wire": MachineSpec(crafting_time=0.5, yield_per_craft=2.0, machine_speed=0.75)
            },
        )
        print("--- Leontief Solver Demo (Circuits, Wire, Plates) ---")
        result = solve_leontief(req)
        print(_format_dict(result))
        return

    if file_path:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    elif json_input:
        data = json.loads(json_input)
    else:
        print("Please provide --demo, --json '<json_str>', or a path to a JSON file.")
        return

    req = LeontiefRequest.model_validate(data)
    result = solve_leontief(req)
    print(_format_dict(result))


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

    # Leontief Factory Calculator
    leontief_parser = subparsers.add_parser(
        "leontief", help="Solve Leontief input-output production balance equation"
    )
    leontief_parser.add_argument("file", nargs="?", help="Path to JSON file with LeontiefRequest")
    leontief_parser.add_argument("--json", dest="json_str", help="JSON string of LeontiefRequest")
    leontief_parser.add_argument(
        "--demo", action="store_true", help="Run the circuits/wire/plates demonstration"
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
    elif args.command == "leontief":
        run_leontief(
            json_input=getattr(args, "json_str", None),
            file_path=getattr(args, "file", None),
            demo=getattr(args, "demo", False),
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
