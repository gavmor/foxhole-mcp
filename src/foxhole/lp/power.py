"""Story 4 — power as a commodity (MW·s). OWNER: story-4. Do not edit from other stories."""

from __future__ import annotations

import math

from foxhole.lp.model import POWER  # noqa: F401  (re-exported for callers)

POWER_OUTPUT = "Facility Power"  # wiki Production Output name for power-plant recipes


def _float(s: str) -> float:
    """Parse a wiki string field as float; blank or missing → 0.0."""
    return float(s) if s and s.strip() else 0.0


def consumed_per_run(row: dict[str, str]) -> float:
    """MW·s a recipe run consumes: InputPower (MW) * ProductionTime (s); 0 if unpowered.

    `row` is a raw wiki Production row (strings). Missing/blank values mean 0.
    """
    return _float(row.get("InputPower", "")) * _float(row.get("ProductionTime", ""))


def produced_per_run(row: dict[str, str]) -> float:
    """MW·s a power-plant run yields: OutputAmount (MW) * ProductionTime (s).

    Only for rows whose Output is POWER_OUTPUT; 0 otherwise.
    """
    if row.get("Output", "") != POWER_OUTPUT:
        return 0.0
    return _float(row.get("OutputAmount", "")) * _float(row.get("ProductionTime", ""))


def plants_needed(mw: float, plant_row: dict[str, str]) -> int:
    """Minimum integer number of plants required to continuously supply `mw` MW.

    Raises ValueError if plant_row is not a power-plant row or has zero output.
    """
    if plant_row.get("Output", "") != POWER_OUTPUT:
        raise ValueError(f"Not a power-plant row: Output={plant_row.get('Output')!r}")
    output_mw = _float(plant_row.get("OutputAmount", ""))
    if output_mw <= 0:
        raise ValueError("Plant has zero or negative OutputAmount")
    return math.ceil(mw / output_mw)


def fuel_per_hour(mw: float, plant_row: dict[str, str]) -> dict[str, float]:
    """Fuel items burned per real hour to supply `mw` MW continuously.

    ASSUMES plants burn fuel only for the energy they deliver (the wiki does not say;
    Update 1.56 only says consumers slow in proportion when supply falls short), so fuel
    is linear in demand and this is a minimum. See docs/lp-power.md.
    Fuel per hour = (mw * 3600 / energy_per_run) * fuel_per_run.

    Raises ValueError for non-power-plant rows or rows with zero energy output.
    """
    if plant_row.get("Output", "") != POWER_OUTPUT:
        raise ValueError(f"Not a power-plant row: Output={plant_row.get('Output')!r}")
    energy_per_run = produced_per_run(plant_row)
    if energy_per_run <= 0:
        raise ValueError("Plant row has zero energy output (check OutputAmount and ProductionTime)")
    runs_per_hour = (mw * 3600.0) / energy_per_run
    result: dict[str, float] = {}
    for i in range(1, 7):
        item = plant_row.get(f"InputItem{i}", "").strip()
        amount_str = plant_row.get(f"InputItem{i}Amount", "").strip()
        if item and amount_str:
            amount = _float(amount_str)
            if amount > 0:
                result[item] = amount * runs_per_hour
    return result
