"""MCP tools for stockpile inventories: Foxhole saves and xurxogr/foxhole-stockpiles exports."""

import logging
from typing import Any

from foxhole.economy import get_economy_solver
from foxhole.stockpiles import (
    default_save_path,
    diff_since_last,
    find_save_files,
    inventory,
    load_quota,
    quota_diff,
    read_stockpiles,
)
from foxhole.telemetry import get_tracer
from foxhole.tools.base import BaseToolProvider

logger = logging.getLogger(__name__)
tracer = get_tracer("foxhole")

_SAVE_NOTE = (
    "The game records a stockpile's contents in the save only once it is pinned and has been "
    "opened in game; empty pinned stockpiles usually just haven't been opened yet."
)


def _source(path: str | None) -> str:
    return path or str(default_save_path())


class StockpileTools(BaseToolProvider):
    """Stockpile reading, change tracking and inventory-aware production planning."""

    async def find_foxhole_saves(self) -> dict[str, Any]:
        """List Foxhole map save files (pinned stockpiles) found on this machine, newest first.

        Searches Steam/Proton on Linux (including Flatpak Steam and extra Steam libraries) and
        %LOCALAPPDATA% on Windows; FOXHOLE_SAVE_PATH overrides. The newest save is used by the
        other stockpile tools whenever no path is given.
        """
        saves = find_save_files()
        return {"saves": [s.model_dump() for s in saves], "note": _SAVE_NOTE}

    async def read_stockpile(
        self,
        path: str | None = None,
        stockpile_names: list[str] | None = None,
        hex_name: str | None = None,
        include_reserves: bool = True,
    ) -> dict[str, Any]:
        """Read pinned stockpiles from the Foxhole save (default) or a foxhole-stockpiles export.

        With no path, reads the newest Foxhole save on this machine, snapshotting it in memory so
        a save being written is never half-read. Also accepts the foxhole-stockpiles app's
        JSON/CSV/TSV exports (OCR or clipboard scans). Resolves game CodeNames to wiki names and
        converts crated quantities into single units.

        Args:
            path: Save or export file; omit to use the newest Foxhole save
            stockpile_names: Only these stockpiles (case-insensitive names)
            hex_name: Only stockpiles in this hex (e.g. 'SpeakingWoodsHex')
            include_reserves: Include reserve stockpiles (default: True)
        """
        try:
            source = _source(path)
            with tracer.start_as_current_span(
                "read_stockpile", attributes={"foxhole.path": source}
            ):
                snaps = read_stockpiles(source, stockpile_names, hex_name, include_reserves)
        except ValueError as e:
            return {"error": str(e)}
        result: dict[str, Any] = {
            "source": source,
            "stockpiles": [s.model_dump(exclude_none=True) for s in snaps],
            "inventory": {k: round(v, 2) for k, v in sorted(inventory(snaps).items())},
        }
        if source.endswith(".sav") and any(not s.entries for s in snaps):
            result["note"] = _SAVE_NOTE
        return result

    async def stockpile_changes(
        self,
        path: str | None = None,
        hex_name: str | None = None,
        include_reserves: bool = True,
    ) -> dict[str, Any]:
        """Report what changed in pinned stockpiles since this tool last read them.

        The first call records a baseline. Each later call lists stockpiles newly pinned
        ('added'), unpinned ('removed'), or whose contents moved ('changed'), with the units
        gained or lost per item. Call it again after a logi run or a play session.

        Args:
            path: Save or export file; omit to use the newest Foxhole save
            hex_name: Only stockpiles in this hex
            include_reserves: Include reserve stockpiles (default: True)
        """
        try:
            source = _source(path)
            snaps = read_stockpiles(source, None, hex_name, include_reserves)
        except ValueError as e:
            return {"error": str(e)}
        scope = f"{source}|{hex_name or '*'}|{'r' if include_reserves else 'p'}"
        return {"source": source, **diff_since_last(snaps, scope)}

    async def stockpile_quota_diff(
        self,
        desired: dict[str, float] | None = None,
        quota_file: str | None = None,
        path: str | None = None,
        stockpile_names: list[str] | None = None,
        hex_name: str | None = None,
        include_reserves: bool = True,
        crates: bool = False,
        include_unlisted: bool = False,
    ) -> dict[str, Any]:
        """Diff desired stock levels against what pinned stockpiles actually hold, as JSON.

        For each desired item: desired, available, delta (available - desired), status
        (short / met / surplus) and whole crates short or spare. Also returns `shortfall` and
        `surplus` maps. `shortfall` (or `shortfall_units` in crate mode) can be passed straight
        to `calculate_required_resources` or `plan_from_stockpile` as the demand.

        Args:
            desired: Target levels {item name, alias or CodeName: quantity}
            quota_file: JSON file with {name: qty} or {"desired": {...}}, instead of `desired`
            path: Save or export file; omit to use the newest Foxhole save
            stockpile_names: Only count these stockpiles
            hex_name: Only count stockpiles in this hex (e.g. 'SpeakingWoodsHex')
            include_reserves: Count reserve stockpiles too (default: True)
            crates: Read and report quantities in crates instead of single units
            include_unlisted: Also list stocked items the quota doesn't mention
        """
        if (desired is None) == (quota_file is None):
            return {"error": "Give exactly one of `desired` or `quota_file`."}
        try:
            spec: dict[str, float] | str = desired if desired is not None else str(quota_file)
            quota = load_quota(spec)
            source = _source(path)
            snaps = read_stockpiles(source, stockpile_names, hex_name, include_reserves)
        except ValueError as e:
            return {"error": str(e)}
        result = quota_diff(quota, snaps, crates=crates, include_unlisted=include_unlisted)
        return {
            "source": source,
            "stockpiles_read": [
                " @ ".join(x for x in (s.name or s.type, s.hex) if x) or "unnamed" for s in snaps
            ],
            **result,
        }

    async def plan_from_stockpile(
        self,
        demand: dict[str, float],
        path: str | None = None,
        stockpile_names: list[str] | None = None,
        hex_name: str | None = None,
        include_reserves: bool = True,
        round_to_crates: bool = False,
        include_machine_counts: bool = False,
        time_window_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Bill of materials for a demand, net of what pinned stockpiles already hold.

        Reads the stockpiles (as `read_stockpile`; newest Foxhole save by default), issues
        finished goods already in stock, then nets the remaining recipe tree against stocked
        intermediates and raw resources (e.g. stocked Basic Materials cancel the Salvage that
        would make them). Reports what was drawn from stock (`inventory_used`) and only the
        shortfall to produce.

        Args:
            demand: Desired goods and quantities {item_name_or_alias: quantity}
            path: Save or export file; omit to use the newest Foxhole save
            stockpile_names: Only draw from these stockpiles
            hex_name: Only draw from stockpiles in this hex (e.g. 'SpeakingWoodsHex')
            include_reserves: Draw from reserve stockpiles too (default: True)
            round_to_crates: Round production of crate-made goods up to whole crates
            include_machine_counts: Compute facility counts for the shortfall
            time_window_seconds: Time budget for machine counts (default 3600s)
        """
        try:
            source = _source(path)
            snaps = read_stockpiles(source, stockpile_names, hex_name, include_reserves)
            plan = get_economy_solver().solve(
                demand=demand,
                include_machine_counts=include_machine_counts,
                time_window_seconds=time_window_seconds,
                round_to_crates=round_to_crates,
                inventory=inventory(snaps),
            )
        except ValueError as e:
            return {"error": str(e)}
        result = plan.model_dump(exclude_none=True)
        result["source"] = source
        result["stockpiles_read"] = [
            " @ ".join(x for x in (s.name or s.type, s.hex) if x) or "unnamed" for s in snaps
        ]
        warnings = [w for s in snaps for w in s.warnings]
        if warnings:
            result["stockpile_warnings"] = warnings
        return result


default_stockpile_tools = StockpileTools()


def __getattr__(name: str):
    """PEP 562: delegate attribute access to the default singleton instance."""
    return getattr(default_stockpile_tools, name)
