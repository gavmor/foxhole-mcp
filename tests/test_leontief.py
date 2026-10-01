"""Unit tests for the Leontief input-output linear solver."""

import json

import pytest

from foxhole.leontief import (
    LeontiefRequest,
    MachineSpec,
    solve_leontief,
)
from foxhole.server import solve_leontief as mcp_solve_leontief


def test_solve_leontief_exact_example():
    """Verify exact user example with Circuits, Wire, and Plates."""
    req = LeontiefRequest(
        items=["circuit", "wire", "plate"],
        coefficients_matrix=[
            [0.0, 0.0, 0.0],
            [3.0, 0.0, 0.1],
            [1.0, 0.0, 0.0],
        ],
        external_demand={
            "circuit": 10.0,
            "wire": 0.0,
            "plate": 5.0,
        },
        machines={
            "wire": MachineSpec(
                crafting_time=0.5,
                yield_per_craft=2.0,
                machine_speed=0.75,
            )
        },
    )

    result = solve_leontief(req)

    # Gross production rates x
    assert result["gross_production_rate"]["circuit"] == 10.0
    assert result["gross_production_rate"]["wire"] == 31.5
    assert result["gross_production_rate"]["plate"] == 15.0

    # Internal consumption rates c = Ax
    assert result["internal_consumption_rate"]["circuit"] == 0.0
    assert result["internal_consumption_rate"]["wire"] == 31.5
    assert result["internal_consumption_rate"]["plate"] == 10.0

    # Net export rates d
    assert result["net_export_rate"]["circuit"] == 10.0
    assert result["net_export_rate"]["wire"] == 0.0
    assert result["net_export_rate"]["plate"] == 5.0

    # Machine calculations N = (x * t) / (y * s)
    # wire: (31.5 * 0.5) / (2.0 * 0.75) = 15.75 / 1.5 = 10.5
    assert result["machine_counts"] is not None
    assert result["machine_counts"]["wire"]["fractional_machines"] == 10.5
    assert result["machine_counts"]["wire"]["integer_machines"] == 11


def test_matrix_dimension_mismatch():
    req = LeontiefRequest(
        items=["a", "b"],
        coefficients_matrix=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        external_demand={"a": 1.0, "b": 1.0},
    )
    with pytest.raises(ValueError, match="Matrix dimension mismatch"):
        solve_leontief(req)


def test_missing_demand_item():
    req = LeontiefRequest(
        items=["iron", "steel"],
        coefficients_matrix=[[0.0, 2.0], [0.0, 0.0]],
        external_demand={"copper": 5.0},
    )
    with pytest.raises(ValueError, match="not found in items list"):
        solve_leontief(req)


def test_hawkins_simon_violation():
    """Economies requiring more input than output violate Hawkins-Simon condition."""
    req = LeontiefRequest(
        items=["fuel"],
        coefficients_matrix=[[1.5]],  # 1.5 fuel needed to make 1 fuel
        external_demand={"fuel": 10.0},
    )
    with pytest.raises(ValueError, match="Hawkins-Simon condition violated"):
        solve_leontief(req)


def test_singular_matrix_error():
    """Singular loop matrix where det(I - A) == 0."""
    req = LeontiefRequest(
        items=["energy"],
        coefficients_matrix=[[1.0]],  # Exactly 1.0 energy needed to make 1.0 energy
        external_demand={"energy": 10.0},
    )
    with pytest.raises(ValueError, match="Failed to invert Leontief matrix"):
        solve_leontief(req)


def test_mcp_tool_wrapper():
    """Test solve_leontief MCP server tool."""
    res_str = mcp_solve_leontief(
        items=["circuit", "wire", "plate"],
        coefficients_matrix=[
            [0.0, 0.0, 0.0],
            [3.0, 0.0, 0.1],
            [1.0, 0.0, 0.0],
        ],
        external_demand={"circuit": 10.0, "wire": 0.0, "plate": 5.0},
        machines={"wire": MachineSpec(crafting_time=0.5, yield_per_craft=2.0, machine_speed=0.75)},
    )
    data = json.loads(res_str)
    assert "gross_production_rate" in data
    assert data["gross_production_rate"]["wire"] == 31.5
    assert data["machine_counts"]["wire"]["integer_machines"] == 11
