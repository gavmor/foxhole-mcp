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

Tools 2–5 answer from the synced Cargo tables (`foxhole cargo-sync`) when available, matching on name, page title, internal codename (e.g. `TruckW`) or wiki alias, and preferring non-deprecated entries. Recipes come from the `Production` table keyed on output, so recipes listed on another item's page (e.g. Gas Mask Filter on the Gas Mask page) are found. Cargo rows carry no flavour quote, so `description` is omitted for cached results.
7. `edit_wiki_page(title: str, content: str, summary: str = ..., section: str | None = None, minor: bool = False, bot: bool = False, createonly: bool = False, nocreate: bool = False)`: Create or edit pages via MediaWiki `action=edit` API with automatic CSRF token negotiation, session auth, retry on token expiration, and cache invalidation.

#### MediaWiki Authentication
Read operations require no authentication. For writing or editing pages with `edit_wiki_page`:
- `FOXHOLE_WIKI_USERNAME` (or `MEDIAWIKI_USERNAME`): Account username or Bot Password identifier (`User@BotName`).
- `FOXHOLE_WIKI_PASSWORD` (or `MEDIAWIKI_PASSWORD`): Account password or Bot Password secret.


### War API Telemetry Tools ([`clapfoot/warapi`](https://github.com/clapfoot/warapi))
1. `get_war_status(shard: str = "live-1")`: Query live World Conquest status, war number, active winner, and victory town requirements.
2. `get_war_casualties(map_name: str | None = None, shard: str = "live-1")`: Query live player casualties and enlistments for a specific hex, or aggregated globally across all 53 active fronts.
3. `get_active_maps(shard: str = "live-1")`: List all active World Conquest map hexes.
4. `get_map_intel(map_name: str, filter_category: str | None = None, shard: str = "live-1")`: Retrieve base control, victory points, logistics assets, and resource fields for any hex.
5. `get_victory_town_status(shard: str = "live-1")`: Compute global victory town scores (Wardens vs Colonials) and scorched town deductions.

### Production Planning Tools
1. `plan_production(target, quantity, recipe_overrides, recipe_choice)`: Full bill of materials from live wiki recipes. Rolls up a DAG in topological order with integer batch rounding, reports production steps, facility load, and alternative recipes; falls back to the Leontief solve $x = (I - A)^{-1}d$ only when the recipe graph has a feedback loop.
2. `calculate_required_resources(demand, include_machine_counts, time_window_seconds, round_to_crates)`: BOM from the precompiled economy registry. After `foxhole cargo-sync`, the registry is built from the wiki's [Cargo](https://foxhole.wiki.gg/wiki/Special:CargoTables) `Production` table (every RecipeRank 1 recipe) instead of the built-in list. `crates` reports whole crates per demanded item; `round_to_crates` solves for those full crates.

### Optimizing Planner (linear programming)
`optimize_production(targets, objective, mode, max_raw, banned_items, deny_recipes, integer, time_window_seconds, max_facilities, use_stockpile, hex_name, max_trips, allow_raw)` plans over **all** wiki recipes, not just each item's primary one, using `scipy` (HiGHS):
- **Alternative recipes and byproducts:** 87 outputs have alternatives and 10 recipes have byproducts. It picks the cheapest route, e.g. Gravel from Coal, or from Salvage when Coal is banned or capped.
- **Objectives:** `min_raw` (optionally weighted), `min_mining_time` (Hammer/Sledge seconds), or `max_throughput` of a target bundle in `rate` mode under raw caps.
- **Power as a commodity:** facilities draw MW·s, and Diesel Power Plants and other power plants burn fuel to supply it. The fuel figures are minimums; see `docs/lp-power.md`.
- **`integer=True` (MILP):** whole crates, batches and plant burns, and whole facilities in a time window, with optional `max_facilities` caps.
- **Raw inputs:** limited to what can be mined or harvested unless listed in `allow_raw`. It can also net against your stockpiles (`use_stockpile`) and cap Loadlugger trips (`max_trips`).

Restricted to rank-1 recipes, it reproduces `calculate_required_resources` exactly. See `docs/lp-planner.md` for worked examples.

### In-Game Time
Times default to the game's own clock, as shown at the bottom left of the Map Screen ("Day 27, 0627 Hours"). One in-game day lasts one real hour (wiki: *Day-Night Cycle*). The **day** comes from the War API's `warReport.dayOfWar`. The **clock within the day** comes from a phase calibration: each `dayOfWar` observation bounds it, and a single reading from the game pins it exactly. The calibration is stored per war in `~/.cache/foxhole/ingame_clock.json`, so offline tools (save timestamps, stockpile changes) are labelled too.
1. `get_ingame_time(shard, at_utc=None)`: Current, or converted, in-game day and clock, with `plus_minus_minutes` uncertainty.
2. `calibrate_ingame_clock(day, time_hhmm, observed_at_utc=None, shard)`: Pin the clock to a Map Screen reading. It's rejected if it contradicts `dayOfWar`. A UTC time given only to the minute adds ±12 in-game minutes of uncertainty.

`get_war_status` reports `now_ingame` and `start_time_ingame`, `get_war_casualties` reports `now_ingame`, and the stockpile tools add `modified_ingame`, `timestamp_ingame` and `previous_read_ingame`. UTC fields are kept alongside.

### Stockpile Tools ([`xurxogr/foxhole-stockpiles`](https://github.com/xurxogr/foxhole-stockpiles))
[foxhole-stockpiles](https://github.com/xurxogr/foxhole-stockpiles) captures in-game stockpiles by OCR screenshot, the game's *Copy to Clipboard*, or the `.sav` save file, and exports them as JSON/CSV/TSV. Items are keyed by game CodeName, which matches the wiki Cargo `codename`, so contents resolve straight onto wiki names, crate sizes and recipes (run `foxhole cargo-sync` first).
1. `find_foxhole_saves()`: Foxhole `MapData.sav` files on this machine, newest first. It searches Steam/Proton on Linux (including Flatpak Steam and extra libraries from `libraryfolders.vdf`) and `%LOCALAPPDATA%` on Windows. Set `FOXHOLE_SAVE_PATH` (a file or a folder) to override.
2. `read_stockpile(path=None, stockpile_names, hex_name, include_reserves)`: Pinned stockpiles in wiki terms, with crated quantities converted to units and a combined `inventory`. With no `path`, it reads the newest save from an in-memory snapshot, retrying if the game writes mid-read. It also accepts foxhole-stockpiles JSON/CSV/TSV exports.
3. `stockpile_changes(path=None, hex_name, include_reserves)`: What changed since the last call. It reports stockpiles `added` (pinned), `removed` (unpinned) or `changed`, with the units gained or lost per item. The first call records a baseline. State is kept in `~/.cache/foxhole/stockpiles/`.
4. `plan_from_stockpile(demand, path=None, ...)`: Bill of materials **net of stock**. Finished goods on hand are issued first, then the recipe tree is netted against stocked intermediates and raw materials (stocked Basic Materials cancel their Salvage). Reports `inventory_used` and only the shortfall to produce.

5. `stockpile_quota_diff(desired | quota_file, path=None, hex_name, crates, include_unlisted, ...)`: **Desired vs available** as JSON. For each quota item it returns desired, available, `delta` (available − desired), `status` (`short` / `met` / `surplus`) and whole `crates_short` / `crates_spare`. It also returns `shortfall` and `surplus` maps, plus `counts` and any `unresolved` names. Quota names may be wiki names, aliases or CodeNames, and each line records how its name was resolved. `shortfall` (`shortfall_units` in crate mode) is ready to pass as `demand` to `calculate_required_resources` or `plan_from_stockpile`. A quota file is `{name: qty}` or `{"desired": {name: qty}}`.

> The game records a stockpile's contents in the save only once it is **pinned and has been opened** in game. A freshly pinned stockpile shows 0 items until then.

`calculate_required_resources` also takes an `inventory` dict directly. Reading `.sav` files needs the optional Rust parser: `uv sync --extra stockpiles`.

### Game Log Tools

Foxhole writes client-side logs under Steam/Proton at
`~/.steam/debian-installation/steamapps/compatdata/505460/pfx/drive_c/users/steamuser/AppData/Local/Foxhole/Saved/Logs/`.
The server discovers them automatically (Steam roots, `libraryfolders.vdf`, Flatpak Steam, Windows `%LOCALAPPDATA%`).
Set `FOXHOLE_LOG_PATH` to override discovery.

#### What is (and isn't) in the logs

| Recorded | Not recorded |
| :--- | :--- |
| Session connections (shard, timestamp) | Kills and deaths |
| Hex region entries (auth/deploy) | Building and construction |
| Border crossings (hex travel) | Crafting and production |
| Deploy (respawn) events | Item pickups |
| Server queue events | Chat messages |

#### MCP tools

1. `archive_game_logs(dest=None)`: Copy game logs from Steam/Proton into an archive directory
   (default: `FOXHOLE_LOG_ARCHIVE` env var, then `~/Documents/The 56th/logs`). Idempotent and
   non-destructive. The live `War.log` and its rotation backup are deduped by the `Log file open`
   timestamp — only the larger copy is kept. Set `FOXHOLE_LOG_PATH` to override the source.

2. `get_session_timeline(since=None, until=None, archive_dir=None)`: Session timeline from
   archived logs. Returns sessions with ordered hex region entries, time spent in each region
   (UTC + in-game "Day N, HHMM Hours" when the clock is calibrated), border crossing counts,
   and deploy counts. `since`/`until` filter by session start time (ISO 8601 UTC).

#### Log format details

- **Header line** (first line): `Log file open, MM/DD/YY HH:MM:SS` — **local time**.
- **All other lines**: `[YYYY.MM.DD-HH.MM.SS:mmm][frame]Category: message` — **UTC**.
- Region server names lack the `Hex` suffix (`SpeakingWoods` → `SpeakingWoodsHex`). Exceptions:
  `MarbanHollow` and `HomeRegion*` servers keep their names as-is.

#### Privacy

Logs contain your Steam ID (numeric and base64) and IP addresses. These are never printed in
tool output. The archive copies your own log files as-is; redaction applies only to what the
tools surface.

#### CLI

```bash
# Archive logs from Steam/Proton into the default directory
uv run foxhole logs archive

# Archive into a custom directory
uv run foxhole logs archive --dest ~/my-logs

# Show session timeline
uv run foxhole logs timeline

# Filter to a specific time window (UTC)
uv run foxhole logs timeline --since 2026-09-29T00:00:00Z --until 2026-10-01T00:00:00Z
```

### Prompts
1. `combat_intel(vehicle_or_weapon: str)`: In-depth combat evaluation, penetration analysis, and counter-tactics.
2. `logistics_plan(item_name: str, requested_amount: int)`: Compute crate counts, material costs, and delivery plans.
3. `strategic_war_overview(shard: str = "live-1")`: Synthesize high-level war situation report across all fronts.
4. `frontline_intel(map_name: str, shard: str = "live-1")`: Generate operational sector briefing for a specific frontline hex.
5. `production_planner(target: str, quantity: float)`: Plan a multi-tier production chain with `plan_production`.
6. `bill_of_materials(item_or_vehicle: str, quantity: float)`: BOM via `calculate_required_resources`.

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

# --- Production Planner ---
uv run foxhole plan "Silverhand Chieftain - Mk. VI" -q 2
uv run foxhole plan "Construction Materials" -q 30 --choice '{"Construction Materials": 1}'
uv run foxhole plan "Dunne Transport" --overrides '{"Basic Materials": {}}'

# --- Cargo Data Sync ---
# Download the wiki's Production, itemdata, vehicles and structures Cargo tables once
# (cached in $FOXHOLE_CARGO_DIR, default ~/.cache/foxhole/cargo); re-run after game updates.
# Once synced, the stats/recipe tools and the resources solver read the cache and only
# fall back to fetching pages for names it doesn't contain.
uv run foxhole cargo-sync
uv run foxhole resources "7.92mm" -q 20 --crates

# --- Stockpiles (foxhole-stockpiles exports or .sav) ---
uv run foxhole saves                         # where's my save?
uv run foxhole stockpile --hex SpeakingWoodsHex  # newest save, pinned stockpiles in a hex
uv run foxhole stockpile --changes           # what changed since the last --changes
uv run foxhole stockpile --hex SpeakingWoodsHex --desired quota.json   # desired vs available (JSON)
uv run foxhole stockpile ~/stockpiles/tine.csv   # or a foxhole-stockpiles export
uv run foxhole resources "Gunner's Breastplate" -q 7 --crates --stockpile ~/stockpiles/tine.csv
```

---

## Telemetry

The Foxhole MCP server includes optional **OpenTelemetry (OTel)** instrumentation to observe server performance, diagnose latency bottlenecks across upstream MediaWiki and WarAPI queries, and understand tool usage patterns.

> [!NOTE]
> **Privacy First & Opt-In by Default**: Telemetry is **disabled by default**. No traces, metrics, or telemetry network requests are emitted unless you explicitly opt in via CLI flags or environment variables. No user credentials, PII, prompt parameters, or chat payloads are collected.

### Enabling Telemetry

You can enable telemetry either via command-line flags or environment variables:

#### 1. Command-Line Flags
```bash
# Enable telemetry when launching the MCP server
uv run foxhole mcp --telemetry

# Or using the alias
uv run foxhole mcp --enable-telemetry

# Enable telemetry during standalone CLI commands
uv run foxhole --telemetry plan "Silverhand - Mk. IV"
```

#### 2. Environment Variables
Set `FOXHOLE_TELEMETRY=1` (or `true`) in your environment or MCP client configuration:

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
        "mcp",
        "--telemetry"
      ],
      "env": {
        "FOXHOLE_TELEMETRY": "1",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
        "OTEL_SERVICE_NAME": "foxhole"
      }
    }
  }
}
```

### Disabling Telemetry (Opt-Out)

If telemetry has been enabled in your shell or parent environment, you can explicitly disable or suppress it:

- **CLI Flag**: Pass `--no-telemetry`:
  ```bash
  uv run foxhole mcp --no-telemetry
  ```
- **Environment Variables**:
  - `FOXHOLE_TELEMETRY=0` (or `false`, `off`, `disabled`)
  - `FASTMCP_TELEMETRY_MODE=off`
  - `OTEL_SDK_DISABLED=true`

### Configuration Reference

| Parameter / Variable | Type / Values | Default | Description |
| :--- | :--- | :--- | :--- |
| `--telemetry` / `--no-telemetry` | CLI Flag | Disabled | Explicitly enable or disable OpenTelemetry instrumentation. |
| `--enable-telemetry` | CLI Flag | Disabled | Alias for `--telemetry`. |
| `--telemetry-mode` | `on` \| `off` \| `propagation_only` | `on` (when enabled) | Controls FastMCP span emission and W3C trace context extraction. |
| `FOXHOLE_TELEMETRY` | `1`/`0`, `true`/`false` | Unset (disabled) | Primary opt-in environment toggle for OpenTelemetry. |
| `FASTMCP_TELEMETRY_MODE` | `on` \| `off` \| `propagation_only` | `on` | FastMCP telemetry mode. Setting to `off` disables all spans. |
| `OTEL_SDK_DISABLED` | `true` \| `false` | `false` | Standard OpenTelemetry SDK disable switch. |
| `OTEL_SERVICE_NAME` | string | `foxhole` | Overrides the OpenTelemetry resource `service.name`. |
| `OTEL_TRACES_EXPORTER` | `otlp` \| `console` \| `memory` \| `none` | `otlp` (or `console`) | Trace exporter backend. Defaults to OTLP when endpoint is configured. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | URL | `http://localhost:4318` | Target OTLP collector HTTP endpoint. |

### Exported Data

When telemetry is enabled, the server emits standard semantic convention spans:
- **MCP Server Spans**: Emitted for incoming MCP tool executions (`tools/call` for `search_foxhole_wiki`, `get_vehicle_stats`, `plan_production`, etc.) and prompt generations (`prompts/get` for `combat_intel`, `logistics_plan`, etc.). Includes tool name, execution latency, and error status codes.
- **Child Operation Spans**: Emitted for internal compute-intensive sub-operations, such as `plan_production.solve` and `calculate_required_resources.solve`.
- **HTTP Client Spans**: Captured automatically via `HTTPXClientInstrumentor` for outbound HTTP requests to `foxhole.wiki.gg` and `war-service-live.foxholeservices.com`, detailing HTTP method, target URL, response status code, and latency.
- **Context Propagation**: Propagates W3C Trace Context headers (`traceparent`, `tracestate`) across distributed traces and LLM client sessions.

**Zero Sensitive Data**: Exported spans contain execution metadata and timings only. Raw wiki articles, prompt parameters, generated content, and user credentials are never captured as span attributes.

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
│       ├── gamelogs.py    # Game log discovery, archival, parsing, and timeline builder
│       ├── models.py      # Pydantic data schemas (Vehicle, Item, Structure, Recipe)
│       ├── parser.py      # wikitextparser template & structure extractor
│       ├── server.py      # MCP server orchestrator & factory (create_server)
│       ├── prompts.py     # MCP prompt templates (combat, logistics, war briefing, BOM)
│       ├── telemetry.py   # OpenTelemetry setup, middleware & HTTPX instrumentation
│       ├── leontief.py    # Base NumPy Leontief (I - A)x = d linear solver
│       ├── planner.py     # Dynamic recursive wiki production planner
│       ├── cli.py         # Command-line interface for Wiki, War API, BOM & planner
│       ├── tools/         # Modular MCP tool components
│       │   ├── __init__.py
│       │   ├── gamelogs.py # Game log MCP tools (GameLogTools)
│       │   ├── wiki.py    # MediaWiki tools component (WikiTools)
│       │   ├── warapi.py  # War API telemetry component (WarApiTools)
│       │   └── production.py # Production planning & BOM tools
│       └── warapi/        # clapfoot/warapi integration
│           ├── __init__.py
│           ├── client.py  # Async War API client with ETag support
│           ├── constants.py# Shards, Icon IDs (5..92), Map Flags
│           └── models.py  # WarState, WarReport, MapItem, GlobalCasualties
└── tests/
    ├── test_economy.py    # Curried Leontief BOM & multiplier tests
    ├── test_parser.py     # Parser unit tests
    ├── test_planner.py    # Production planner tests
    ├── test_server.py     # MCP tools integration tests
    ├── test_telemetry.py  # OpenTelemetry unit & integration tests
    ├── test_warapi.py     # War API unit & integration tests
    └── test_leontief.py   # Leontief solver unit tests
```
