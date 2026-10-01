"""MCP prompt templates for Foxhole intelligence, logistics, production planning, and BOM."""

from mcp.server.mcpserver import MCPServer

from foxhole.warapi import DEFAULT_SHARD


def combat_intel(vehicle_or_weapon: str) -> str:
    """Prompt template for analyzing combat strengths, vulnerabilities, and counter-tactics."""
    return f"""Please provide a comprehensive tactical breakdown of '{vehicle_or_weapon}' in Foxhole:
1. Review its health pool, armor type, and penetration chances.
2. Detail its armament(s), ammo requirements, firing range, and fire rate.
3. List subsystem disable vulnerabilities (e.g., track disable chance).
4. Recommend optimal engagement tactics, counter-vehicles, and logistical support needed."""


def logistics_plan(item_name: str, requested_amount: int = 100) -> str:
    """Prompt template for calculating material inputs and shipping crates."""
    return f"""Please create a logistics production and transport plan for {requested_amount} units of '{item_name}':
1. Lookup the crate packaging size and calculate the number of crates needed.
2. Calculate total raw and refined materials required (bmats, rmats, explosive powder, etc.).
3. Determine production facility (Factory vs Mass Production Factory vs Facility) and time required.
4. Provide recommendations for shipping container and truck transport."""


def strategic_war_overview(shard: str = DEFAULT_SHARD) -> str:
    """Prompt template for synthesizing live war situation reports."""
    return f"""Please generate a high-level strategic intelligence briefing on Foxhole Shard '{shard}':
1. Check current War number, status (Active vs Resistance Phase), and victor if concluded.
2. Review global casualty totals, casualty ratio between Wardens and Colonials, and identify the most contested fronts.
3. Check Victory Town control and proximity to conquest victory condition.
4. Highlight major logistical bottlenecks or tactical opportunities for the front lines."""


def frontline_intel(map_name: str, shard: str = DEFAULT_SHARD) -> str:
    """Prompt template for detailed sector intelligence on a specific hex."""
    return f"""Please provide a tactical sector report for hex '{map_name}' on Foxhole Shard '{shard}':
1. Fetch latest casualties and enlistments to gauge front-line intensity.
2. Review base control distribution between Colonials and Wardens.
3. Identify presence of Victory Towns, scorched bases, or rocket targets.
4. Map key logistics assets (factories, refineries, seaports) and strategic approach angles."""


def production_planner(target: str, quantity: float = 1) -> str:
    """Prompt template for planning a multi-tier production chain."""
    return f"""Please plan production of {quantity}x '{target}' in Foxhole:
1. Call `plan_production(target={target!r}, quantity={quantity})`.
2. Report raw material totals and each production step with its facility and batch count.
3. If `alternative_recipes` lists cheaper options (e.g. Metal Press, Smelter), re-run with `recipe_choice` and compare.
4. Flag any `unresolved_inputs` and give hauling advice for the raw materials."""


def bill_of_materials(item_or_vehicle: str, quantity: float = 1.0) -> str:
    """Prompt template for calculating the full Leontief bill of materials and raw scrap requirements."""
    return f"""Please compute the comprehensive bill of materials and logistical requirements for {quantity}x '{item_or_vehicle}':
1. Call `calculate_required_resources(demand={{{item_or_vehicle!r}: {quantity}}}, include_machine_counts=True)` to solve the precompiled Leontief economy matrix.
2. Report the primary raw resource totals (Salvage, Components, Sulfur, Crude Oil, Coal).
3. Detail intermediate goods and facility modification stages (e.g. base vehicle chassis, PCmats, Assembly Materials).
4. Review the physical machine and facility counts needed (Small Assembly Station, Garage, Refinery, Metalworks).
5. Provide actionable logistics advice on hauling, refining, and crate packaging."""


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


def register_prompts(server: MCPServer) -> None:
    """Register all prompt templates with the given MCP server."""
    server.prompt()(combat_intel)
    server.prompt()(logistics_plan)
    server.prompt()(strategic_war_overview)
    server.prompt()(frontline_intel)
    server.prompt()(production_planner)
    server.prompt()(bill_of_materials)
    server.prompt()(leontief_facility_planner)
