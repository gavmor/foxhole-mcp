"""Story 6 — MCP tool for the LP planner. OWNER: story-6."""

from __future__ import annotations

import logging
from typing import Any

from foxhole.economy import get_economy_solver
from foxhole.lp.model import Limits, Mode, Objective, Target
from foxhole.tools.base import BaseToolProvider

logger = logging.getLogger(__name__)


def _error(msg: str) -> dict[str, Any]:
    return {"error": msg}


def _build_summary(result_dict: dict[str, Any], mode: Mode, objective: Objective) -> str:
    status = result_dict.get("status", "unknown")
    if status != "optimal":
        return f"Solver returned status: {status}"
    runs = result_dict.get("runs", {})
    raw_used = result_dict.get("raw_used", {})
    obj_val = result_dict.get("objective_value")

    lines = [f"Status: {status} | Objective: {objective} | Mode: {mode}"]
    if obj_val is not None:
        lines.append(f"Objective value: {obj_val:.4g}")
    if runs:
        run_strs = ", ".join(f"{r} x {v:.3g}" for r, v in runs.items())
        lines.append(f"Recipes: {run_strs}")
    if raw_used:
        raw_strs = ", ".join(f"{v:.3g} {k}" for k, v in raw_used.items())
        lines.append(f"Raw inputs: {raw_strs}")
    throughput = result_dict.get("throughput")
    if throughput is not None:
        lines.append(f"Throughput: {throughput:.4g} bundles/hr")
    facilities = result_dict.get("facilities", {})
    if facilities:
        fac_strs = ", ".join(f"{v:.2g} {k}" for k, v in facilities.items())
        lines.append(f"Facilities needed: {fac_strs}")
    return " | ".join(lines)


def _build_recipes_chosen(
    result_dict: dict[str, Any],
    recipes_by_output: dict[str, list[str]],
) -> list[dict[str, str]]:
    """For each used recipe, note the id and a reason when alternatives existed."""
    chosen = []
    for recipe_id, runs in result_dict.get("runs", {}).items():
        output = recipe_id.split("#")[0] if "#" in recipe_id else recipe_id
        alternatives = [r for r in recipes_by_output.get(output, []) if r != recipe_id]
        if alternatives:
            reason = f"chosen over {len(alternatives)} alternative(s): {', '.join(alternatives)}"
        else:
            reason = "only recipe for this output"
        chosen.append({"recipe_id": recipe_id, "runs": runs, "reason": reason})
    return chosen


class OptimizeTools(BaseToolProvider):
    """Optimising production planner (alternative recipes, byproducts, caps, power)."""

    async def optimize_production(
        self,
        targets: dict[str, float],
        objective: str = "min_raw",
        mode: str = "quantity",
        max_raw: dict[str, float] | None = None,
        banned_items: list[str] | None = None,
        deny_recipes: list[str] | None = None,
        integer: bool = False,
        time_window_seconds: float = 3600.0,
        max_facilities: dict[str, int] | None = None,
        use_stockpile: bool = False,
        hex_name: str | None = None,
        max_trips: float | None = None,
        allow_raw: list[str] | None = None,
    ) -> dict[str, Any]:
        """Plan production by linear programming over ALL wiki recipes.

        Loads the full wiki Production table, builds the recipe matrix, and solves an LP
        (or MILP when integer=True) to satisfy the requested output targets.

        Args:
            targets: Output items and quantities, e.g. {"Basic Materials": 500}.
            objective: "min_raw" | "min_mining_time" | "max_throughput".
            mode: "quantity" (totals) | "rate" (per real hour).
            max_raw: Per-item cap on raw intake.
            banned_items: Items to exclude entirely.
            deny_recipes: Recipe ids that may not run.
            integer: Solve as MILP (whole runs per recipe).
            time_window_seconds: Planning horizon (default 3600 s = 1 hr).
            max_facilities: Per-source machine caps (MILP only).
            use_stockpile: If True, read local stockpile file and net inventory first.
            hex_name: Filter stockpile to this hex (requires use_stockpile=True).
            max_trips: Add a hauling cap: total raw fits in this many truck trips.
            allow_raw: Extra raw inputs to allow. By default raw intake is limited to what can
                be mined or harvested (Salvage, Components, Sulfur, Coal, Oil, Water, ...);
                other unproduced inputs (Critically Wounded Soldier, damaged aircraft parts,
                Rare Metal, ...) are excluded, and so are recipes that need them.

        Returns:
            LPResult fields plus "summary" (human text) and "recipes_chosen" list.
        """
        # --- Validate enums early so we return error dicts, not exceptions ---
        try:
            obj_enum = Objective(objective)
        except ValueError:
            valid = ", ".join(o.value for o in Objective)
            return _error(f"Unknown objective {objective!r}; valid: {valid}")

        try:
            mode_enum = Mode(mode)
        except ValueError:
            valid = ", ".join(m.value for m in Mode)
            return _error(f"Unknown mode {mode!r}; valid: {valid}")

        # --- Load production rows ---
        from foxhole.cargo import load_production

        rows = load_production()
        if rows is None:
            return _error("Production table not cached. Run `foxhole cargo-sync` first.")

        # --- Build recipe matrix ---
        from foxhole.lp.matrix import build_recipe_matrix

        try:
            matrix = build_recipe_matrix(rows)
        except Exception as exc:
            return _error(f"Failed to build recipe matrix: {exc}")

        # --- Resolve target names ---
        solver = get_economy_solver()
        resolved_targets: list[Target] = []
        for raw_name, qty in targets.items():
            try:
                canonical = solver.resolve_item_name(raw_name)
            except ValueError as exc:
                return _error(str(exc))
            resolved_targets.append(Target(item=canonical, quantity=float(qty)))

        if not resolved_targets:
            return _error("No targets specified.")

        # --- Build Limits ---
        # Only gatherable resources may enter from outside unless explicitly allowed
        from foxhole.lp.model import POWER

        non_gatherable = matrix.raw_items - matrix.extractable - set(allow_raw or []) - {POWER}
        limits = Limits(
            max_raw=max_raw or {},
            banned_items=set(banned_items or []) | non_gatherable,
            denied_recipes=set(deny_recipes) if deny_recipes else set(),
        )
        notes: list[str] = []

        # --- Stockpile inventory ---
        inv: dict[str, float] | None = None
        if use_stockpile:
            try:
                from foxhole.stockpiles import inventory as inv_fn
                from foxhole.stockpiles import read_stockpiles

                snaps = read_stockpiles(None, hex_name=hex_name)
                inv = inv_fn(snaps)
            except Exception as exc:
                notes.append(f"Stockpile not used: {exc}")
                inv = None

        # --- Hauling constraint ---
        if max_trips is not None:
            try:
                from foxhole.lp.hauling import trip_constraint

                limits.extra.append(trip_constraint(max_trips))
            except Exception as exc:
                return _error(f"Failed to build hauling constraint: {exc}")

        # --- Solve ---
        try:
            if integer:
                from foxhole.lp.integer import solve_milp

                result = solve_milp(
                    matrix,
                    resolved_targets,
                    objective=obj_enum,
                    mode=mode_enum,
                    limits=limits,
                    inventory=inv,
                    time_window_seconds=time_window_seconds,
                    max_facilities=max_facilities,
                )
            else:
                from foxhole.lp.solve import solve_lp

                result = solve_lp(
                    matrix,
                    resolved_targets,
                    objective=obj_enum,
                    mode=mode_enum,
                    limits=limits,
                    inventory=inv,
                    time_window_seconds=time_window_seconds,
                )
        except Exception as exc:
            return _error(f"Solver error: {exc}")

        # --- Trips report ---
        trips: int | None = None
        if max_trips is not None and result.raw_used:
            try:
                from foxhole.lp.hauling import trips_needed

                trips = trips_needed(result.raw_used)
            except Exception as exc:
                logger.warning("trips_needed failed: %s", exc)

        # --- Build recipes_chosen ---
        recipes_by_output: dict[str, list[str]] = {}
        for r in matrix.recipes:
            output = r.id.split("#")[0] if "#" in r.id else r.id
            recipes_by_output.setdefault(output, []).append(r.id)

        result_dict = result.model_dump()
        summary = _build_summary(result_dict, mode_enum, obj_enum)
        recipes_chosen = _build_recipes_chosen(result_dict, recipes_by_output)

        if result.status == "infeasible":
            why = []
            if banned_items:
                why.append(f"banned: {', '.join(sorted(banned_items))}")
            if max_raw:
                why.append(f"raw caps: {max_raw}")
            if max_trips is not None:
                why.append(f"at most {max_trips:g} Loadlugger trips of raw resources")
            if max_facilities:
                why.append(f"facility caps: {max_facilities}")
            why.append(
                "only mineable/harvestable raw inputs are allowed (add others via allow_raw)"
            )
            notes.append("No plan satisfies all constraints. Active: " + "; ".join(why) + ".")
        result_dict["notes"] = [*result_dict.get("notes", []), *notes]
        out: dict[str, Any] = {**result_dict, "summary": summary, "recipes_chosen": recipes_chosen}
        if trips is not None:
            out["trips_needed"] = trips
        return out


default_optimize_tools = OptimizeTools()


def __getattr__(name: str):
    """PEP 562: delegate attribute access to the default singleton instance."""
    return getattr(default_optimize_tools, name)
