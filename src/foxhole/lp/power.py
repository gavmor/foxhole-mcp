"""Story 4 — power as a commodity (MW·s). OWNER: story-4. Do not edit from other stories."""

from __future__ import annotations

from foxhole.lp.model import POWER  # noqa: F401  (re-exported for callers)

POWER_OUTPUT = "Facility Power"  # wiki Production Output name for power-plant recipes


def consumed_per_run(row: dict[str, str]) -> float:
    """MW·s a recipe run consumes: InputPower (MW) * ProductionTime (s); 0 if unpowered.

    `row` is a raw wiki Production row (strings). Missing/blank values mean 0.
    """
    raise NotImplementedError("story-4")


def produced_per_run(row: dict[str, str]) -> float:
    """MW·s a power-plant run yields: OutputAmount (MW) * ProductionTime (s).

    Only for rows whose Output is POWER_OUTPUT; 0 otherwise.
    """
    raise NotImplementedError("story-4")
