"""Command-line interface for Foxhole MCP Server and wiki tools."""

import argparse
import asyncio
import json
import sys

from foxhole.client import FoxholeWikiClient
from foxhole.parser import parse_item, parse_page_content, parse_structure, parse_vehicle
from foxhole.server import _resolve_title, server


def _format_dict(d: dict) -> str:
    return json.dumps(d, indent=2)


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


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="foxhole",
        description="Foxhole MediaWiki MCP Server & structured CLI reference",
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

    # Search command
    search_parser = subparsers.add_parser("search", help="Search the Foxhole wiki")
    search_parser.add_argument("query", help="Search query string")
    search_parser.add_argument("-n", "--limit", type=int, default=5, help="Number of results")

    # Vehicle lookup
    veh_parser = subparsers.add_parser("vehicle", help="Look up vehicle specifications")
    veh_parser.add_argument("name", help="Vehicle name or variant")

    # Item lookup
    item_parser = subparsers.add_parser("item", help="Look up item or weapon specifications")
    item_parser.add_argument("name", help="Item name")

    # Structure lookup
    struct_parser = subparsers.add_parser("structure", help="Look up structure specifications")
    struct_parser.add_argument("name", help="Structure name")

    # Page lookup
    page_parser = subparsers.add_parser("page", help="Get clean text and overview of a wiki page")
    page_parser.add_argument("title", help="Page title")

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
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
