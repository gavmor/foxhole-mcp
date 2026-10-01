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

### 3. Run Quality Checks (`ruff` + `ty` + `pytest`)

```bash
# Check code style & lints
uv run ruff check

# Format check
uv run ruff format --check

# Type check using Astral's ty
uv run ty check

# Run test suite
uv run pytest
```

### 4. Git Hooks via Lefthook

Hooks are configured in [lefthook.yml](file:///home/user/Documents/foxhole/lefthook.yml):
- **Pre-commit**: Auto-fixes lints with `ruff check --fix`, formats staged files with `ruff format`, runs `ty check`, and runs `pytest`.
- **Pre-push**: Runs full linting, format verification, `ty` type checking, and the complete test suite in parallel.

Install and test hooks:
```bash
lefthook install
lefthook run pre-commit
lefthook run pre-push
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
        "FOXHOLE_USER_AGENT": "FoxholeMCP/0.1.0 (+https://github.com/gavmor/foxhole-mcp; game-assistant)"
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

### MediaWiki Tools
1. `search_foxhole_wiki(query: str, limit: int = 5)`: Search for articles across `foxhole.wiki.gg`.
2. `get_vehicle_stats(vehicle_name: str)`: Return structured specifications, subsystem disable chances, and armaments.
3. `get_item_stats(item_name: str)`: Return weapon stats, damage types, crate sizes, and ammunition types.
4. `get_structure_stats(structure_name: str)`: Return structure HP, decay duration, repair cost, and defenses.
5. `get_production_cost(name: str)`: Extract exact manufacturing requirements, cycle times, and facility sources.
6. `get_page_overview(title: str)`: Return a clean text overview of any page without wiki markup.

### War API Telemetry Tools ([`clapfoot/warapi`](https://github.com/clapfoot/warapi))
1. `get_war_status(shard: str = "live-1")`: Query live World Conquest status, war number, active winner, and victory town requirements.
2. `get_war_casualties(map_name: str | None = None, shard: str = "live-1")`: Query live player casualties and enlistments for a specific hex, or aggregated globally across all 53 active fronts.
3. `get_active_maps(shard: str = "live-1")`: List all active World Conquest map hexes.
4. `get_map_intel(map_name: str, filter_category: str | None = None, shard: str = "live-1")`: Retrieve base control, victory points, logistics assets, and resource fields for any hex.
5. `get_victory_town_status(shard: str = "live-1")`: Compute global victory town scores (Wardens vs Colonials) and scorched town deductions.

### Factory Optimization Tools (NumPy Leontief Solver)
1. `solve_leontief(items, coefficients_matrix, external_demand, machines)`: Solves the linear production balance equation $(I - A)x = d$ using `np.linalg.solve(I - A, d)`. Calculates gross rates ($x$), internal consumption ($c = Ax$), and physical building counts ($N = \frac{xt}{ys}$) with Hawkins-Simon viability checks.

### Prompts
1. `combat_intel(vehicle_or_weapon: str)`: In-depth combat evaluation, penetration analysis, and counter-tactics.
2. `logistics_plan(item_name: str, requested_amount: int)`: Compute crate counts, material costs, and delivery plans.
3. `strategic_war_overview(shard: str = "live-1")`: Synthesize high-level war situation report across all fronts.
4. `frontline_intel(map_name: str, shard: str = "live-1")`: Generate operational sector briefing for a specific frontline hex.
5. `leontief_facility_planner(target_production: str)`: Formulate and solve multi-tier industrial facility chains.

---

## CLI Usage

You can query both the wiki and live game telemetry directly from the command line:

```bash
# --- War API Live Telemetry ---
# Check current World Conquest status
uv run foxhole war

# Check casualties for a specific hex
uv run foxhole casualties --map DeadLandsHex

# Aggregate casualties across all active fronts
uv run foxhole casualties

# List all active hexes
uv run foxhole maps

# Tactical intelligence for a hex
uv run foxhole intel DeadLandsHex

# Victory town score
uv run foxhole victory

# --- MediaWiki Game Data ---
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

# --- Leontief Factory Calculator ---
# Run circuits / wire / plate demonstration
uv run foxhole leontief --demo

# Solve custom facility plan from file or string
uv run foxhole leontief plan.json
uv run foxhole leontief --json '{"items": ["fuel", "oil"], "coefficients_matrix": [[0, 2], [0, 0]], "external_demand": {"fuel": 10}}'
```

---

## Cross-Domain Intelligence & Real-World Q&A

A major advantage of `foxhole-mcp` is synthesizing **static MediaWiki game mechanics** (item recipes, crate capacities, vehicle armor ratings) with **live War API telemetry** (base control, town status, frontline casualties, and sector infrastructure).

Below are three verified real-world examples demonstrating how an LLM agent answers multi-layered operational questions using this MCP server:

---

### Example 1: Frontline Logistics & Supply Chain Calculation

> **Officer Query:**  
> *"We need to supply the frontline in Dead Lands with 1,000 rounds of 40mm and 20 Loughcaster rifles. What facilities in that hex can make them, what raw materials do we need to haul, and how many crates is that?"*

#### 1. Live Hex Telemetry & Facility Recon (`War API: get_map_intel`)
* **Sector Control:** Wardens hold the central Victory Point, **Town Base 1** at Abandoned Ward (`(0.4200, 0.4824)`).
* **Production Facilities:** Dead Lands contains an active **World Factory** in Abandoned Ward (`(0.4057, 0.5116)`). Both Small Arms and Heavy Ammunition queues are operational directly in-hex.
* **Depot Proximity:** An in-hex **Storage Facility** sits at `(0.4030, 0.5017)`, allowing bulk delivery and storage.

#### 2. Item & Crate Breakdown (`MediaWiki: get_item_stats`, `get_production_cost`)
* **40mm Light Tank Ammunition:**
  * 20 rounds per crate (`crate_amount: 20`).
  * 1,000 rounds $\div$ 20/crate = **50 crates**.
  * Factory Cost: 160 Basic Materials (Bmats) + 240 Explosive Powder (Emats) per crate.
  * Subtotal: **8,000 Bmats** + **12,000 Explosive Powder**.
* **No.2 Loughcaster Service Rifle:**
  * 20 rifles per crate (`crate_amount: 20`).
  * 20 rifles $\div$ 20/crate = **1 crate**.
  * Factory Cost: 100 Bmats per crate.
  * Subtotal: **100 Bmats**.

#### 3. Hauling & Transport Optimization
* **Total Manifest:** **51 crates** | **8,100 Bmats** | **12,000 Explosive Powder**.
* **Transport Options:**
  * **Standard Truck (Dunne Transport):** 15 slots/trip $\rightarrow$ **4 truck trips**.
  * **Intermodal (Flatbed Truck + Shipping Container):** 60 slots/container $\rightarrow$ **1 single trip** delivering all 51 crates with 9 slots remaining.

---

### Example 2: Tactical Combat Assessment & Armor Doctrine

> **Officer Query:**  
> *"Casualties in Marban Hollow are heavily contested. If Colonials are fielding 85K-b “Falchion” / Spatha tanks, what Warden armored vehicles should we pull from the garage, what are their penetration chances and weak spots, and how many Rmats do they cost?"*

#### 1. Recommended Vehicle: **Silverhand - Mk. IV** (`MediaWiki: get_vehicle_stats`)
* **Production Source:** **Vehicle Garage (160 Rmats)** (No facility modification delay).
* **Role:** Line Breaker Assault Tank with dual 40mm turret + 68mm hull gun.

#### 2. Specifications & Direct Tank Matchup

| Metric | Warden: Silverhand - Mk. IV | Colonial: 85K-b “Falchion” | Colonial: 85K-a “Spatha” |
| :--- | :--- | :--- | :--- |
| **Production Source** | **Garage (160 Rmats)** | Garage (135 Rmats) | Facility Upgrade (Falchion + PCMs) |
| **Hit Points (HP)** | 3,100 HP | 3,650 HP | 3,650 HP |
| **Armor Health Pool** | **17,000 HP** | 12,725 HP | 10,500 HP |
| **Min Penetration Chance** | **25.0%** (Highest deflection) | 33.0% | 33.0% |
| **Max Penetration Chance** | 67.0% | 67.0% | 70.0% |
| **Disable Threshold** | 30.0% (930 HP remaining) | 30.0% (1,095 HP remaining) | 30.0% (1,095 HP remaining) |
| **Armament** | **40mm Cannon** (40m, 360°) +<br>**68mm AT Gun** (35m, Hull mount) | 40mm Cannon (40m, 360°) | HV 40mm Cannon (45m, 360°) |
| **Crew Needed** | 4 (Driver, Commander, 40mm, 68mm) | 3 (Driver, Commander, Gunner) | 3 (Driver, Commander, Gunner) |

#### 3. Tactical Engagement Doctrine
1. **The 35m Dual-Gun Alpha Strike:** Maintain distance between **34m and 35m** where both the 40mm turret and the 68mm hull gun can fire simultaneously for a devastating **1,200 alpha burst damage** (~33% of a Colonial tank's HP in a single volley).
2. **Deflection Angling:** Face the frontal hull directly toward incoming fire to utilize the 17,000 armor pool and 25% min penetration chance; never expose rear or side plates where penetration rates spike to 67%.
3. **Protect the Tracks:** The Silverhand has a **30.0% track disable chance**. If tracked, its hull-mounted 68mm gun (restricted to a 40° horizontal traverse) can be bypassed by agile flankers.

---

### Example 3: Strategic War Room Telemetry & Victory Audit

> **Command Query:**  
> *"What is the strategic situation on the active shard? Who holds the victory towns, what are the most contested frontline sectors, and how many casualties have occurred globally?"*

#### 1. Live Conquest Status (`War API: get_war_status`, `get_victory_town_status`)
* **War Number:** **War 140** (Shard `live-1` / Able)
* **Status:** **Resistance Phase** (World Conquest concluded; victor: **COLONIALS**).
* **Victory Towns Held:** Wardens: **13** | Colonials: **10** (Target: 34; Scorched: 0).

#### 2. Global Casualty Telemetry (`War API: get_war_casualties`)
* **Total Global Casualties:** **1,277 casualties** across 1,155 player enlistments.
  * **Colonial Casualties:** 594 (46.5%)
  * **Warden Casualties:** 683 (53.5%)
  * **Attrition Delta:** +89 Warden losses.

#### 3. Top Contested Frontlines
* **Dead Lands (`DeadLandsHex`):** **1,176 casualties** (**92.1%** of all server casualties) — ground combat centered at *Abandoned Ward*, *The Pits*, and *The Spine*.
* **Loch Mór (`LochMorHex`):** 30 casualties (2.3%).
* **The Heartlands (`HeartlandsHex`):** 19 casualties (1.5%).

---

### Example 4: Bill of Materials & Leontief Production (Bike-Mounted MG & Tanks)

> **Officer Query:**  
> *"Determine the total raw resources and intermediate facility parts needed for a bike-mounted machine gun."*

#### 1. Why Currying Matrix $A$ is Necessary
Generic Leontief tools expect the caller to formulate the ordered sector list, technical coefficients matrix $A$, and demand vector $d$. For natural language agents, this creates an impossible chicken-and-egg problem: the agent would need to already know all sub-recipes (Caster motorcycle, Construction Materials, Salvage ratios) and hand-craft an $N \times N$ matrix.

By **currying the $N \times N$ Foxhole technical coefficients matrix $A$ at compile/startup time**, we precompute the Leontief multiplier matrix:
$$L = (I - A)^{-1}$$
The agent simply calls `calculate_required_resources({"bike-mounted machine gun": 1.0})`, and the server performs an instantaneous $O(N^2)$ dot product $x = L \cdot d$.

#### 2. BOM Breakdown (`calculate_required_resources`, `foxhole resources`)

```bash
uv run foxhole resources "bike-mounted machine gun" --quantity 1 --machines
```

* **Resolved Entity:** **00MS “Stinger”** (Colonial 7.92mm MG Motorcycle).
* **Raw Resources:**
  * **Salvage:** **220.0 Salvage** (170 for 85 Bmats + 50 for 5 Cmats).
* **Refined & Facility Materials:**
  * **Basic Materials (Bmats):** 85.0 (for 03MM “Caster” chassis).
  * **Construction Materials (Cmats):** 5.0 (for Small Assembly modification).
* **Intermediate Production:**
  * **03MM “Caster”:** 1.0 vehicle assembled at Garage.
* **Required Facility Stations:**
  * **Garage:** 1x base vehicle craft (30s).
  * **Small Assembly Station:** 1x weapon upgrade (180s).

---

## Project Structure

```text
foxhole/
├── pyproject.toml         # uv project configuration, dependencies & ruff rules
├── lefthook.yml           # Git hooks (ruff, ty, pytest)
├── mcp_config.json        # MCP server client configuration
├── README.md              # Documentation & Architecture
├── src/
│   └── foxhole/
│       ├── __init__.py    # Package exports
│       ├── client.py      # Async MediaWiki client with TTL caching & user-agent
│       ├── economy.py     # Precompiled Curried Leontief solver (A, L = (I-A)^-1)
│       ├── models.py      # Pydantic data schemas (Vehicle, Item, Structure, Recipe)
│       ├── parser.py      # wikitextparser template & structure extractor
│       ├── server.py      # FastMCP server exposing Wiki tools, War API & BOM
│       ├── leontief.py    # Base NumPy Leontief (I - A)x = d linear solver
│       ├── cli.py         # Command-line interface for Wiki, War API, BOM & Leontief
│       └── warapi/        # clapfoot/warapi integration
│           ├── __init__.py
│           ├── client.py  # Async War API client with ETag support
│           ├── constants.py# Shards, Icon IDs (5..92), Map Flags
│           └── models.py  # WarState, WarReport, MapItem, GlobalCasualties
└── tests/
    ├── test_economy.py    # Curried Leontief BOM & multiplier tests
    ├── test_parser.py     # Parser unit tests
    ├── test_server.py     # MCP tools integration tests
    ├── test_warapi.py     # War API unit & integration tests
    └── test_leontief.py   # Leontief solver unit tests
```
