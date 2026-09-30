# Foxhole MediaWiki MCP Server & CLI 🦊⚙️

A custom **Model Context Protocol (MCP)** server and CLI tool for [foxhole.wiki.gg](https://foxhole.wiki.gg).

Unlike generic MediaWiki MCP servers that return unparsed wikitext or entire web pages, this server extracts and parses **Foxhole-specific structures** into clean, typed, token-efficient JSON data for LLMs and command-line usage.

---

## Features

- **Domain-Specific Parsing**:
  - **Vehicles** (`Vehicle Infobox`): Hit points, armor type, armor health, bounce/penetration rates (`min_pen_chance`, `max_pen_chance`), subsystem disable chances (tracks, turret, fuel tank), fuel capacity/burn rates, road/off-road speeds, armaments, and garage/facility production costs.
  - **Items & Weapons** (`Item Infobox`): Kinetic/explosive damage profiles, compatible ammunition, magazine capacity, rate of fire, effective/max range, encumbrance, crate packaging counts, and factory/MPF manufacturing costs.
  - **Structures** (`Structure Infobox`): Structure HP, armor rating, decay duration, repair costs, mounted defenses, and building materials.
  - **Logistics Recipes** (`PRD1_`, `PRD2_`): Production facilities (Garage, Factory, MPF, Small Assembly Station), required resources (bmats, rmats, facility mats), power consumption, and production cycle durations.
- **Fast & Token-Efficient**:
  - Strips wiki navigation boilerplate, styling markup, and clutter before returning data to the LLM.
  - In-memory TTL caching to minimize requests to `foxhole.wiki.gg`.
  - Configurable custom `User-Agent` header complying with wiki.gg bot policies.
- **Dual Mode**:
  - Runs as an **MCP Server** over standard I/O (`stdio`) or Server-Sent Events (`sse`).
  - Functions as a stand-alone **CLI tool** for fast terminal lookups.
- **Modern Python Tooling**:
  - Managed with [`uv`](https://docs.astral.sh/uv/) for lightning-fast dependency resolution and virtual environments.
  - Formatted and linted with [`ruff`](https://docs.astral.sh/ruff/).
  - Fully tested with `pytest` and `pytest-asyncio`.

---

## Quick Start

### 1. Prerequisites

Ensure `uv` is installed:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Setup & Virtual Environment

From this directory:
```bash
uv sync
```

### 3. Run Quality Checks (`ruff` + `pytest`)

```bash
# Check code style & lints
uv run ruff check

# Format check
uv run ruff format --check

# Run test suite
uv run pytest
```

---

## MCP Server Configuration

To connect this server to your MCP client (Claude Desktop, Claude Code, Antigravity, Cursor, etc.), add the server to your `mcpServers` configuration:

```json
{
  "mcpServers": {
    "foxhole-wiki": {
      "command": "uv",
      "args": [
        "--directory",
        "/home/user/Documents/foxhole",
        "run",
        "foxhole",
        "mcp"
      ],
      "env": {
        "FOXHOLE_USER_AGENT": "FoxholeMCP/0.1.0 (+https://github.com/foxhole/foxhole-mcp; game-assistant)"
      }
    }
  }
}
```

### SSE Transport (Optional)

If running as a background service with HTTP/SSE:
```bash
uv run foxhole mcp --transport sse --port 8000
```

---

## MCP Tools & Prompts Exposed

### Tools
1. `search_foxhole_wiki(query: str, limit: int = 5)`: Search for articles across `foxhole.wiki.gg`.
2. `get_vehicle_stats(vehicle_name: str)`: Return structured specifications, subsystem disable chances, and armaments.
3. `get_item_stats(item_name: str)`: Return weapon stats, damage types, crate sizes, and ammunition types.
4. `get_structure_stats(structure_name: str)`: Return structure HP, decay duration, repair cost, and defenses.
5. `get_production_cost(name: str)`: Extract exact manufacturing requirements, cycle times, and facility sources.
6. `get_page_overview(title: str)`: Return a clean text overview of any page without wiki markup.

### Prompts
1. `combat_intel(vehicle_or_weapon: str)`: Prompts the model for an in-depth combat evaluation, penetration analysis, and counter-tactics.
2. `logistics_plan(item_name: str, requested_amount: int)`: Prompts the model to compute crate counts, material costs, and delivery plans.

---

## CLI Usage

You can also query the wiki directly from your command line:

```bash
# Search wiki
uv run foxhole search "Storm Cannon"

# Vehicle statistics
uv run foxhole vehicle "Silverhand - Mk. IV"
uv run foxhole vehicle "Dunne Transport"

# Weapon / Item statistics
uv run foxhole item "No.2 Loughcaster"
uv run foxhole item "40mm"

# Structure statistics
uv run foxhole structure "Storm Cannon"

# Clean page text
uv run foxhole page "Artillery"
```

---

## Project Structure

```text
foxhole/
├── pyproject.toml         # uv project configuration, dependencies & ruff rules
├── mcp_config.json        # MCP server client configuration
├── README.md              # Documentation
├── src/
│   └── foxhole/
│       ├── __init__.py    # Package exports
│       ├── client.py      # Async MediaWiki client with TTL caching & user-agent
│       ├── models.py      # Pydantic data schemas (Vehicle, Item, Structure, Recipe)
│       ├── parser.py      # wikitextparser template & structure extractor
│       ├── server.py      # FastMCP / MCPServer definitions
│       └── cli.py         # Command-line interface
└── tests/
    ├── test_parser.py     # Parser unit tests
    └── test_server.py     # MCP tools and live integration tests
```
