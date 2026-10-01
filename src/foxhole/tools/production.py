"""MCP tools for Foxhole production planning, Leontief matrix solvers, and Bill of Materials."""

import json
import logging

from mcp.server.mcpserver import MCPServer

from foxhole.economy import get_economy_solver
from foxhole.leontief import (
    LeontiefRequest,
    MachineSpec,
)
from foxhole.leontief import (
    solve_leontief as calculate_leontief,
)
from foxhole.models import ProductionRecipe
from foxhole.parser import (
    parse_item,
    parse_structure,
    parse_vehicle,
)
from foxhole.planner import plan_production as _plan_production
from foxhole.tools.wiki import default_wiki_tools

logger = logging.getLogger(__name__)


async def default_fetch_recipes(name: str) -> tuple[str, list[ProductionRecipe]] | None:
    """Resolve a name to its wiki title and parsed production recipes."""
    title = await default_wiki_tools.resolve_title(name)
    data = await default_wiki_tools.client.get_page_data(title)
    if not data or not data.get("wikitext"):
        return None
    title, wikitext = data["title"], data["wikitext"]
    for parse in (parse_vehicle, parse_item, parse_structure):
        parsed = parse(title, wikitext)
        if parsed and parsed.production:
            return title, parsed.production
    return title, []


def calculate_required_resources(
    demand: dict[str, float],
    include_machine_counts: bool = False,
    time_window_seconds: float | None = None,
) -> str:
    """Calculate total raw resources, refined materials, intermediate components, and facility counts needed to produce any Foxhole vehicle, weapon, ammunition, or facility good.

    Solves the curried Leontief input-output balance equation x = (I - A)^(-1) d at compile/initialization time.
    Provides the complete Bill of Materials (BOM) down to primary resources (Salvage, Components, Sulfur, Coal, Crude Oil).

    Use this tool whenever asked:
    - 'Determine the total resources needed for a bike-mounted machine gun' (00MS "Stinger")
    - 'What materials do I need to build 5 Spathas or Silverhand Chieftains?'
    - 'How much scrap and components are needed for 20 crates of 40mm ammo?'
    - 'Bill of materials for...' or 'Calculate production cost breakdown for...'

    Supports common names and nicknames (e.g. 'bike-mounted machine gun', 'stinger', 'spatha', 'bmats', 'falchion', 'chieftain').

    Args:
        demand: Desired output goods and quantities {item_name_or_alias: quantity}
        include_machine_counts: Whether to compute required physical assembly stations/factories
        time_window_seconds: Time budget in seconds to produce the demand (default: 3600s / 1 hour if machine counts requested)
    """
    try:
        solver = get_economy_solver()
        plan = solver.solve(
            demand=demand,
            include_machine_counts=include_machine_counts,
            time_window_seconds=time_window_seconds,
        )
        return json.dumps(plan.model_dump(exclude_none=True), indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)
    except Exception as e:
        logger.exception("Failed to calculate required resources")
        return json.dumps({"error": f"Internal error solving production demand: {e}"}, indent=2)


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


async def plan_production(
    target: str,
    quantity: float = 1,
    recipe_overrides: dict[str, dict[str, float]] | None = None,
    recipe_choice: dict[str, int] | None = None,
) -> str:
    """Compute the full bill of materials to produce any Foxhole vehicle, item, or structure.

    Recursively pulls recipes from foxhole.wiki.gg, rolls up totals down to raw resources
    (Salvage, Components, Sulfur, Coal, Oil, ...) with integer batch rounding, and reports
    production steps, facility load, and alternative recipes. Feedback loops (e.g. mines
    burning fuel refined from their own output) are solved with a Leontief fallback.

    Use for questions like "how many bmats/salvage for 5 Dunnes?" or "full cost of 20 40mm".

    Args:
        target: Item name or alias (e.g. 'Dunne Transport', '40mm', 'Chieftain')
        quantity: Number of units to produce (default: 1)
        recipe_overrides: Replace recipes: {item: {input: qty per 1 output}}. Use {} to treat an
            item as raw (e.g. {'Basic Materials': {}}), or override a raw resource to model
            extraction (e.g. {'Salvage': {'Diesel': 0.111}}).
        recipe_choice: Pick an alternative wiki recipe by index: {item: index}. Indices are
            listed in the response's alternative_recipes.
    """
    import sys

    server_mod = sys.modules.get("foxhole.server")
    fetch_fn = (
        getattr(server_mod, "_fetch_recipes", default_fetch_recipes)
        if server_mod
        else default_fetch_recipes
    )
    try:
        result = await _plan_production(
            target,
            quantity,
            fetch_fn,
            recipe_overrides=recipe_overrides,
            recipe_choice=recipe_choice,
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)}, indent=2)


def register_production_tools(server: MCPServer) -> None:
    """Register production planning, Leontief solver, and BOM tools with MCPServer."""
    server.add_tool(calculate_required_resources)
    server.add_tool(plan_production)
    server.add_tool(solve_leontief)
