"""Generic logistics algorithms: recipe-matrix LP/MILP production planning and Leontief
input-output balancing. Domain-agnostic -- callers supply recipes and constraints."""

from logistics.hauling import trip_constraint, trips_for_mixed_load, trips_needed
from logistics.integer import solve_milp
from logistics.leontief import (
    LeontiefRequest,
    LeontiefResponse,
    MachineCount,
    MachineSpec,
    solve_leontief,
)
from logistics.model import (
    ExtraConstraint,
    Limits,
    LPResult,
    Mode,
    Objective,
    Recipe,
    RecipeMatrix,
    Target,
)
from logistics.solve import build_balance_constraints, solve_lp

__all__ = [
    "ExtraConstraint",
    "LPResult",
    "LeontiefRequest",
    "LeontiefResponse",
    "Limits",
    "MachineCount",
    "MachineSpec",
    "Mode",
    "Objective",
    "Recipe",
    "RecipeMatrix",
    "Target",
    "build_balance_constraints",
    "solve_leontief",
    "solve_lp",
    "solve_milp",
    "trip_constraint",
    "trips_for_mixed_load",
    "trips_needed",
]
