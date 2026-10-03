"""Production planner: bill-of-materials rollup over the Foxhole recipe graph.

Recipes are pulled on demand from a fetcher (the wiki parser by default) and
expanded into a sparse graph. Acyclic graphs (the common case) are rolled up in
topological order with integer batch rounding. Only when the graph contains a
genuine feedback loop (e.g. Salvage Mines burning Diesel refined from Salvage)
does the planner fall back to the continuous Leontief solve x = (I - A)^-1 d.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from foxhole.leontief import LeontiefRequest, solve_leontief
from foxhole.models import ProductionRecipe

# Items treated as leaves unless explicitly overridden. Their wiki "recipes" are
# extraction processes (mines, harvesters) that would otherwise create cycles.
RAW_RESOURCES = frozenset(
    {
        "Salvage",
        "Components",
        "Damaged Components",
        "Sulfur",
        "Coal",
        "Oil",
        "Crude Oil",
        "Water",
        "Rare Metal",
    }
)

MAX_ITEMS = 200
_EPS = 1e-9

# Async lookup: item name -> (canonical name, recipes), or None if not found.
RecipeFetcher = Callable[[str], Awaitable[tuple[str, list[ProductionRecipe]] | None]]


def _recipe_summary(r: ProductionRecipe) -> dict[str, Any]:
    out: dict[str, Any] = {"source": r.source, "inputs": dict(r.inputs)}
    if r.category:
        out["category"] = r.category
    if r.input_vehicle:
        out["input_vehicle"] = r.input_vehicle
    out["output_amount"] = r.output_amount or 1
    return out


def _recipe_inputs(r: ProductionRecipe) -> dict[str, float]:
    inputs = {k: float(v) for k, v in r.inputs.items()}
    if r.input_vehicle:
        inputs[r.input_vehicle] = inputs.get(r.input_vehicle, 0.0) + 1.0
    return inputs


def _topological_order(edges: dict[str, dict[str, float]], root: str) -> list[str] | None:
    """Kahn's algorithm from consumers to inputs. Returns None if a cycle exists."""
    indegree: dict[str, int] = defaultdict(int)
    for item, inputs in edges.items():
        indegree.setdefault(item, 0)
        for inp in inputs:
            indegree[inp] += 1
    queue = [root] if indegree[root] == 0 else []
    order: list[str] = []
    while queue:
        item = queue.pop()
        order.append(item)
        for inp in edges.get(item, {}):
            indegree[inp] -= 1
            if indegree[inp] == 0:
                queue.append(inp)
    return order if len(order) == len(indegree) else None


async def plan_production(
    target: str,
    quantity: float,
    fetch_recipes: RecipeFetcher,
    recipe_overrides: dict[str, dict[str, float]] | None = None,
    recipe_choice: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Compute the full bill of materials needed to produce `quantity` of `target`.

    Args:
        target: Item, vehicle, or structure name (aliases resolved by the fetcher).
        quantity: Number of units of target to produce.
        fetch_recipes: Async recipe lookup, see RecipeFetcher.
        recipe_overrides: {item: {input: qty_per_one_output_unit}} replacing the wiki
            recipe. An empty dict marks the item as a raw leaf; overriding a raw
            resource (e.g. {"Salvage": {"Diesel": 1/9}}) expands it instead.
        recipe_choice: {item: index} selecting among an item's alternative wiki recipes.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    overrides = recipe_overrides or {}
    choice = recipe_choice or {}

    resolved = await fetch_recipes(target)
    root = resolved[0] if resolved else target
    if root not in overrides and target in overrides:
        overrides = {**overrides, root: overrides[target]}

    # Per-batch inputs and batch size for every producible item in the graph.
    edges: dict[str, dict[str, float]] = {}
    batch_size: dict[str, float] = {}
    chosen: dict[str, ProductionRecipe] = {}
    alternatives: dict[str, list[dict[str, Any]]] = {}
    unresolved: list[str] = []
    leaves: set[str] = set()

    cache: dict[str, tuple[str, list[ProductionRecipe]] | None] = {target: resolved}
    pending = [root]
    seen: set[str] = set()
    while pending:
        item = pending.pop()
        if item in seen:
            continue
        seen.add(item)
        if len(seen) > MAX_ITEMS:
            raise ValueError(f"Recipe graph exceeds {MAX_ITEMS} items; aborting expansion.")

        if item in overrides:
            if not overrides[item]:
                leaves.add(item)
                continue
            edges[item] = {k: float(v) for k, v in overrides[item].items()}
            batch_size[item] = 1.0
        elif item in RAW_RESOURCES:
            leaves.add(item)
            continue
        else:
            if item not in cache:
                cache[item] = await fetch_recipes(item)
            found = cache[item]
            recipes = found[1] if found else []
            if not recipes:
                leaves.add(item)
                unresolved.append(item)
                continue
            idx = choice.get(item, 0)
            if not 0 <= idx < len(recipes):
                raise ValueError(
                    f"recipe_choice[{item!r}]={idx} out of range; {len(recipes)} recipes available."
                )
            recipe = recipes[idx]
            chosen[item] = recipe
            if len(recipes) > 1:
                alternatives[item] = [
                    {"index": i, **_recipe_summary(r)} for i, r in enumerate(recipes)
                ]
            edges[item] = _recipe_inputs(recipe)
            batch_size[item] = float(recipe.output_amount or 1)

        pending.extend(edges[item])

    order = _topological_order(edges, root)
    if order is not None:
        totals, batches = _rollup_dag(order, edges, batch_size, root, quantity)
        method = "bom_rollup"
    else:
        totals, batches = _solve_cyclic(edges, batch_size, leaves, root, quantity)
        method = "leontief"
        order = sorted(totals, key=lambda i: (i in leaves, i))

    steps = []
    facilities: dict[str, dict[str, float]] = defaultdict(
        lambda: {"batches": 0, "busy_seconds": 0.0}
    )
    for item in order:
        if item not in edges:
            continue
        n = batches[item]
        step: dict[str, Any] = {
            "item": item,
            "required": round(totals[item], 4),
            "batches": n,
            "produced": round(n * batch_size[item], 4),
            "inputs_per_batch": edges[item],
        }
        recipe = chosen.get(item)
        if recipe:
            step["source"] = recipe.source
            if recipe.category:
                step["category"] = recipe.category
            if recipe.production_time_sec:
                step["batch_time_sec"] = recipe.production_time_sec
                facilities[recipe.source]["busy_seconds"] += n * recipe.production_time_sec
            facilities[recipe.source]["batches"] += n
        else:
            step["source"] = "override"
        steps.append(step)

    result: dict[str, Any] = {
        "target": root,
        "quantity": quantity,
        "method": method,
        "raw_materials": {
            i: round(totals[i], 4) for i in sorted(leaves) if i not in unresolved and totals.get(i)
        },
        "production_steps": steps,
        "facility_load": {k: dict(v) for k, v in facilities.items()},
    }
    if unresolved:
        result["unresolved_inputs"] = {i: round(totals.get(i, 0.0), 4) for i in sorted(unresolved)}
    if alternatives:
        result["alternative_recipes"] = alternatives
    if method == "leontief":
        result["note"] = (
            "Recipe graph contains a feedback loop; totals are continuous Leontief "
            "solutions and include self-consumption. Batch counts are ceilings."
        )
    return result


def _rollup_dag(
    order: list[str],
    edges: dict[str, dict[str, float]],
    batch_size: dict[str, float],
    root: str,
    quantity: float,
) -> tuple[dict[str, float], dict[str, int]]:
    """Single pass over consumers-before-inputs order with integer batch rounding."""
    totals: dict[str, float] = defaultdict(float)
    totals[root] = quantity
    batches: dict[str, int] = {}
    for item in order:
        if item not in edges:
            continue
        n = math.ceil(totals[item] / batch_size[item] - _EPS)
        batches[item] = n
        for inp, amt in edges[item].items():
            totals[inp] += n * amt
    return dict(totals), batches


def _solve_cyclic(
    edges: dict[str, dict[str, float]],
    batch_size: dict[str, float],
    leaves: set[str],
    root: str,
    quantity: float,
) -> tuple[dict[str, float], dict[str, int]]:
    """Continuous Leontief solve for graphs with feedback loops."""
    items = sorted(set(edges) | leaves)
    index = {name: i for i, name in enumerate(items)}
    a = [[0.0] * len(items) for _ in items]
    for item, inputs in edges.items():
        j = index[item]
        for inp, amt in inputs.items():
            a[index[inp]][j] += amt / batch_size[item]
    solved = solve_leontief(
        LeontiefRequest(items=items, coefficients_matrix=a, external_demand={root: quantity})
    )
    totals = solved["gross_production_rate"]
    batches = {i: math.ceil(totals[i] / batch_size[i] - _EPS) for i in edges}
    return totals, batches
