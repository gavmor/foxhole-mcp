"""Leontief Input-Output linear model solver for factory and facility production chains."""

import math
from typing import Any

import numpy as np
from pydantic import BaseModel, Field


class MachineSpec(BaseModel):
    """Machine and cycle parameters for physical building count calculations."""

    crafting_time: float = Field(description="Base recipe time in seconds (t)")
    yield_per_craft: float = Field(default=1.0, description="Items produced per craft cycle (y)")
    machine_speed: float = Field(default=1.0, description="Machine crafting speed multiplier (s)")


class LeontiefRequest(BaseModel):
    """Input payload for solving the Leontief production balance equation (I - A)x = d."""

    items: list[str] = Field(
        description="Ordered list of sector/item names, e.g. ['circuit', 'wire', 'plate']"
    )
    coefficients_matrix: list[list[float]] = Field(
        description="Matrix A where A[i][j] is the amount of item i needed to produce 1 unit of item j."
    )
    external_demand: dict[str, float] = Field(
        description="Desired net export rate per second {item: demand_rate}"
    )
    machines: dict[str, MachineSpec] | None = Field(
        default=None,
        description="Optional machine recipe and speed parameters per item to calculate building counts",
    )


class MachineCount(BaseModel):
    """Calculated physical machine requirements."""

    fractional_machines: float = Field(description="Exact fractional machine count needed")
    integer_machines: int = Field(description="Ceiling integer machine count needed")


class LeontiefResponse(BaseModel):
    """Solved gross production rates, internal usage, and required machine counts."""

    gross_production_rate: dict[str, float] = Field(
        description="Gross production rate per second (x) needed to satisfy demand and internal loops"
    )
    internal_consumption_rate: dict[str, float] = Field(
        description="Rate per second consumed internally by other production processes (c = Ax)"
    )
    net_export_rate: dict[str, float] = Field(
        description="Net output rate available for export (d = x - c)"
    )
    machine_counts: dict[str, MachineCount] | None = Field(
        default=None,
        description="Physical machine counts required for items where MachineSpec was provided",
    )


def solve_leontief(req: LeontiefRequest) -> dict[str, Any]:
    """Solve the Leontief balance equation (I - A)x = d for gross production rates.

    Uses np.linalg.solve(I - A, d) for high numerical stability and speed.
    Validates matrix dimensions, checks for singular loops, and enforces the
    Hawkins-Simon viability condition (non-negative gross production).
    """
    n = len(req.items)
    item_indices = {item: idx for idx, item in enumerate(req.items)}

    # 1. Validate matrix dimensions
    A = np.array(req.coefficients_matrix, dtype=float)
    if A.shape != (n, n):
        raise ValueError(f"Matrix dimension mismatch: expected ({n}, {n}), got {A.shape}")

    # 2. Build demand vector d
    d = np.zeros(n, dtype=float)
    for item, rate in req.external_demand.items():
        if item not in item_indices:
            raise ValueError(f"External demand item '{item}' not found in items list: {req.items}")
        d[item_indices[item]] = rate

    # 3. Form (I - A) and solve (I - A)x = d
    identity = np.eye(n, dtype=float)
    M = identity - A

    try:
        x = np.linalg.solve(M, d)
    except np.linalg.LinAlgError as e:
        raise ValueError(
            f"Failed to invert Leontief matrix (I - A). Check for singular loop or unviable economy: {e}"
        ) from e

    # 4. Check for Hawkins-Simon viability condition (non-negative gross production)
    if np.any(x < -1e-9):
        negative_items = [req.items[i] for i, val in enumerate(x) if val < -1e-9]
        raise ValueError(
            f"Hawkins-Simon condition violated: Negative gross production required for {negative_items}. "
            "The system consumes more than it produces."
        )

    # Clean near-zero floats
    x = np.where(np.isclose(x, 0), 0.0, x)

    # 5. Compile gross production output
    gross_rates = {req.items[i]: round(float(x[i]), 4) for i in range(n)}

    # 6. Calculate internal consumption: c = A * x
    internal_usage = A @ x
    internal_rates = {req.items[i]: round(float(internal_usage[i]), 4) for i in range(n)}

    # 7. Calculate machine counts if specs provided
    machine_counts = {}
    if req.machines:
        for item, spec in req.machines.items():
            if item in item_indices:
                idx = item_indices[item]
                rate = float(x[idx])
                effective_speed = spec.yield_per_craft * spec.machine_speed
                if effective_speed <= 0:
                    raise ValueError(f"Machine effective speed for {item} must be positive.")
                # N = (x * t) / (y * s)
                raw_count = (rate * spec.crafting_time) / effective_speed
                machine_counts[item] = {
                    "fractional_machines": round(raw_count, 4),
                    "integer_machines": math.ceil(raw_count - 1e-9),
                }

    return {
        "gross_production_rate": gross_rates,
        "internal_consumption_rate": internal_rates,
        "net_export_rate": {req.items[i]: round(float(d[i]), 4) for i in range(n)},
        "machine_counts": machine_counts if machine_counts else None,
    }
