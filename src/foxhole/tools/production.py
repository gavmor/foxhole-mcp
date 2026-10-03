"""MCP tools for Foxhole production planning and Bill of Materials."""

import logging
import math
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from foxhole.cargo import get_cargo_store
from foxhole.economy import get_economy_solver
from foxhole.models import ProductionRecipe
from foxhole.parser import (
    parse_item,
    parse_structure,
    parse_vehicle,
)
from foxhole.planner import plan_production as _plan_production
from foxhole.telemetry import get_tracer
from foxhole.tools.base import BaseToolProvider
from foxhole.tools.wiki import default_wiki_tools

logger = logging.getLogger(__name__)
tracer = get_tracer("foxhole")


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
    round_to_crates: bool = False,
    inventory: dict[str, float] | None = None,
) -> dict[str, Any]:
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
        round_to_crates: Round demanded items up to whole crates before solving (factories only
            produce full crates, e.g. 20 mags of 7.92mm costs a full crate of 30). The `crates`
            field always reports the whole crates needed.
        inventory: Units already on hand {item_name: quantity}; only the shortfall is planned
            and `inventory_used` reports what stock covered. To use a captured stockpile,
            call `plan_from_stockpile` instead.
    """
    try:
        with tracer.start_as_current_span(
            "calculate_required_resources.solve",
            attributes={
                "foxhole.demand_keys": list(demand.keys()),
                "foxhole.include_machines": include_machine_counts,
            },
        ):
            solver = get_economy_solver()
            plan = solver.solve(
                demand=demand,
                include_machine_counts=include_machine_counts,
                time_window_seconds=time_window_seconds,
                round_to_crates=round_to_crates,
                inventory=inventory,
            )
            return plan.model_dump(exclude_none=True)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.exception("Failed to calculate required resources")
        return {"error": f"Internal error solving production demand: {e}"}


def calculate_mpf_cost(
    target: str,
    crates: int = 5,
) -> dict[str, Any]:
    """Calculate Mass Production Factory (MPF) queue costs with progressive crate discounts.

    Each crate queued in an order receives an increasing discount:
    - Crate 1: 10% discount (0.90x)
    - Crate 2: 20% discount (0.80x)
    - Crate 3: 30% discount (0.70x)
    - Crate 4: 40% discount (0.60x)
    - Crate 5+: 50% discount (0.50x)
    Vehicle and structure orders cap at 5 crates (standard: 3 vehicles/crate; Falchion MPT: 5/crate).
    Ammo and item orders can queue up to 9 crates.
    Per-crate costs round down (floor) per game mechanics.

    Args:
        target: Name or alias of vehicle, weapon, ammo, or item (e.g. 'Falchion', 'Silverhand', '40mm')
        crates: Number of crates to queue (1 to 5 for vehicles/structures, up to 9 for items/ammo; default: 5)
    """
    store = get_cargo_store()
    if not store:
        return {"error": "Cargo store not initialized. Run `foxhole cargo-sync`."}

    v = store.vehicle(target)
    item = store.item(target) if not v else None

    if not v and not item:
        return {"error": f"Item or vehicle '{target}' could not be found."}

    is_vehicle = bool(v)
    name = v.name if v else (item.name if item else target)
    max_crates = 5 if is_vehicle else 9
    crates = max(1, min(crates, max_crates))

    base_crate: dict[str, float] = {}
    units_per_crate = 1
    if is_vehicle and v:
        recipes = v.production or store.recipes(v.name)
        if not recipes:
            return {"error": f"No production recipe found for vehicle '{name}'."}
        garage_rec = recipes[0]
        crate_mult = 3.0  # MPF vehicle base crate is 3x single vehicle cost
        base_crate = {k: float(val) * crate_mult for k, val in garage_rec.inputs.items()}
        units_per_crate = 5 if "falchion" in name.lower() else 3
    elif item:
        recipes = item.production or store.recipes(item.name)
        if not recipes:
            return {"error": f"No production recipe found for item '{name}'."}
        rec = recipes[0]
        base_crate = {k: float(val) for k, val in rec.inputs.items()}
        units_per_crate = item.crate_amount or store.crate_capacity(item.name) or 1

    discounts = [0.10, 0.20, 0.30, 0.40, 0.50, 0.50, 0.50, 0.50, 0.50]
    per_crate: list[dict[str, Any]] = []
    total_order_cost: dict[str, float] = {k: 0.0 for k in base_crate}

    for i in range(crates):
        disc = discounts[i]
        c_cost: dict[str, float] = {}
        for mat, amt in base_crate.items():
            cost_rounded = float(math.floor(amt * (1.0 - disc)))
            c_cost[mat] = cost_rounded
            total_order_cost[mat] += cost_rounded
        per_crate.append(
            {
                "crate_number": i + 1,
                "discount_percent": int(disc * 100),
                "cost": c_cost,
            }
        )

    total_units = crates * units_per_crate
    per_unit_cost = {k: round(val / total_units, 2) for k, val in total_order_cost.items()}

    cost_summary_str = ", ".join(
        f"{int(v_val) if v_val.is_integer() else v_val} {k}"
        for k, v_val in total_order_cost.items()
    )
    return {
        "target": name,
        "crates_queued": crates,
        "units_per_crate": units_per_crate,
        "total_units": total_units,
        "base_crate_cost": base_crate,
        "per_crate_costs": per_crate,
        "total_order_cost": total_order_cost,
        "effective_cost_per_unit": per_unit_cost,
        "summary": (
            f"Mass Production Factory order for {crates} crate(s) ({total_units} total units) of '{name}': "
            f"Total order cost is {cost_summary_str}."
        ),
    }


def calculate_hauling_trips(
    demands: dict[str, float],
    vehicle: str = "Dunne Loadlugger 3c",
    vehicle_slots: int | None = None,
) -> dict[str, Any]:
    """Calculate the number of hauling truck trips required for raw materials or items.

    In Foxhole, loose raw resources (Salvage, Components, Sulfur, Coal) stack up to 100 per inventory slot.
    Each item/material fills ceil(quantity / stack_size) slots.
    Standard hauling vehicles:
    - Dunne Loadlugger 3c / BMS - Packmule Flatbed: 20 slots (holds up to 2,000 raw resources)
    - R-5b “Sisyphus” Hauler: 14 slots (holds up to 1,400 raw resources)

    Args:
        demands: Mapping of material or item names to quantities (e.g. {'Salvage': 6315, 'Components': 2100, 'Coal': 200})
        vehicle: Vehicle name or model (default: 'Dunne Loadlugger 3c')
        vehicle_slots: Optional override for vehicle inventory capacity in slots (e.g. 20)
    """
    v_lower = vehicle.lower()
    slots = 20
    if "sisyphus" in v_lower:
        slots = 14
    elif "loadlugger" in v_lower or "flatbed" in v_lower or "packmule" in v_lower:
        slots = 20
    if vehicle_slots is not None:
        slots = vehicle_slots

    stack_size = 100
    slot_breakdown: dict[str, int] = {}
    total_slots = 0
    for mat, amt in demands.items():
        if amt <= 0:
            continue
        s_needed = math.ceil(amt / stack_size)
        slot_breakdown[mat] = s_needed
        total_slots += s_needed

    trips = math.ceil(total_slots / slots) if slots > 0 else 0

    return {
        "vehicle": vehicle,
        "vehicle_slots": slots,
        "stack_size_per_slot": stack_size,
        "material_demands": demands,
        "slot_breakdown": slot_breakdown,
        "total_slots_required": total_slots,
        "trips_needed": trips,
        "summary": (
            f"Hauling {demands} requires {total_slots} total slots ({slot_breakdown}). "
            f"With a {slots}-slot vehicle ({vehicle}), this requires {trips} trip(s)."
        ),
    }


class ProductionTools(BaseToolProvider):
    """Production and bill of materials tool provider."""

    def __init__(self, fetch_fn: Callable = default_fetch_recipes):
        self.fetch_fn = fetch_fn

    def register(self, server: MCPServer) -> None:
        """Register production planning and BOM tools with MCPServer."""
        super().register(server)
        server.add_tool(calculate_required_resources)
        server.add_tool(calculate_mpf_cost)
        server.add_tool(calculate_hauling_trips)

    async def plan_production(
        self,
        target: str,
        quantity: float = 1,
        recipe_overrides: dict[str, dict[str, float]] | None = None,
        recipe_choice: dict[str, int] | None = None,
    ) -> dict[str, Any]:
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
            getattr(server_mod, "_fetch_recipes", self.fetch_fn) if server_mod else self.fetch_fn
        )
        try:
            with tracer.start_as_current_span(
                "plan_production.solve",
                attributes={
                    "foxhole.target": target,
                    "foxhole.quantity": quantity,
                },
            ):
                result = await _plan_production(
                    target,
                    quantity,
                    fetch_fn,
                    recipe_overrides=recipe_overrides,
                    recipe_choice=recipe_choice,
                )
                return result
        except ValueError as e:
            return {"error": str(e)}


default_production_tools = ProductionTools()


async def plan_production(
    target: str,
    quantity: float = 1,
    recipe_overrides: dict[str, dict[str, float]] | None = None,
    recipe_choice: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Module-level convenience wrapper for default_production_tools.plan_production."""
    return await default_production_tools.plan_production(
        target=target,
        quantity=quantity,
        recipe_overrides=recipe_overrides,
        recipe_choice=recipe_choice,
    )


def register_production_tools(server: MCPServer) -> None:
    """Register production planning and BOM tools with MCPServer."""
    default_production_tools.register(server)
