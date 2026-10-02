"""MCP tools for stockpile inventories exported by xurxogr/foxhole-stockpiles."""

import logging
from typing import Any

from foxhole.economy import get_economy_solver
from foxhole.stockpiles import inventory, read_stockpiles
from foxhole.telemetry import get_tracer
from foxhole.tools.base import BaseToolProvider

logger = logging.getLogger(__name__)
tracer = get_tracer("foxhole")


class StockpileTools(BaseToolProvider):
    """Stockpile reading and inventory-aware production planning."""

    async def read_stockpile(
        self,
        path: str,
        stockpile_names: list[str] | None = None,
        hex_name: str | None = None,
        include_reserves: bool = True,
    ) -> dict[str, Any]:
        """Read stockpile contents captured by the foxhole-stockpiles app, in wiki terms.

        Accepts the app's JSON/CSV/TSV exports (from OCR, clipboard or save-file scans) or a
        Foxhole MapData `.sav` file directly (pinned stockpiles). Resolves each item's game
        CodeName to its wiki name and converts crated quantities into single units.

        Args:
            path: Path to the export file or `.sav` file
            stockpile_names: Only these stockpiles (case-insensitive names)
            hex_name: Only stockpiles in this hex (save-file exports carry the hex)
            include_reserves: Include reserve stockpiles (default: True)
        """
        try:
            with tracer.start_as_current_span("read_stockpile", attributes={"foxhole.path": path}):
                snaps = read_stockpiles(path, stockpile_names, hex_name, include_reserves)
        except ValueError as e:
            return {"error": str(e)}
        return {
            "stockpiles": [s.model_dump(exclude_none=True) for s in snaps],
            "inventory": {k: round(v, 2) for k, v in sorted(inventory(snaps).items())},
        }

    async def plan_from_stockpile(
        self,
        path: str,
        demand: dict[str, float],
        stockpile_names: list[str] | None = None,
        hex_name: str | None = None,
        include_reserves: bool = True,
        round_to_crates: bool = False,
        include_machine_counts: bool = False,
        time_window_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Bill of materials for a demand, net of what a stockpile already holds.

        Reads the stockpile (as `read_stockpile`), issues finished goods already in stock,
        then nets the remaining recipe tree against stocked intermediates and raw resources
        (e.g. stocked Basic Materials cancel the Salvage that would make them). Reports what
        was drawn from stock (`inventory_used`) and only the shortfall to produce.

        Args:
            path: Path to the foxhole-stockpiles export or `.sav` file
            demand: Desired goods and quantities {item_name_or_alias: quantity}
            stockpile_names: Only draw from these stockpiles
            hex_name: Only draw from stockpiles in this hex
            include_reserves: Draw from reserve stockpiles too (default: True)
            round_to_crates: Round production of crate-made goods up to whole crates
            include_machine_counts: Compute facility counts for the shortfall
            time_window_seconds: Time budget for machine counts (default 3600s)
        """
        try:
            snaps = read_stockpiles(path, stockpile_names, hex_name, include_reserves)
            stock = inventory(snaps)
            plan = get_economy_solver().solve(
                demand=demand,
                include_machine_counts=include_machine_counts,
                time_window_seconds=time_window_seconds,
                round_to_crates=round_to_crates,
                inventory=stock,
            )
        except ValueError as e:
            return {"error": str(e)}
        result = plan.model_dump(exclude_none=True)
        result["stockpiles_read"] = [s.name or s.type or "unnamed" for s in snaps]
        warnings = [w for s in snaps for w in s.warnings]
        if warnings:
            result["stockpile_warnings"] = warnings
        return result


default_stockpile_tools = StockpileTools()


def __getattr__(name: str):
    """PEP 562: delegate attribute access to the default singleton instance."""
    return getattr(default_stockpile_tools, name)
