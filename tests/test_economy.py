"""Unit tests for the Curried Leontief Economy Solver and BOM calculations."""

import json

import numpy as np
import pytest

from foxhole.economy import get_economy_solver
from foxhole.server import calculate_required_resources


def test_economy_solver_precomputation():
    """Verify matrix dimensions, Hawkins-Simon conditions, and inversion precision."""
    solver = get_economy_solver()
    n = solver.n
    assert n >= 70, f"Expected comprehensive economy with >= 70 items, got {n}"

    # Matrix shapes
    assert solver.A.shape == (n, n)
    assert solver.L.shape == (n, n)

    # Inversion accuracy: (I - A) * L = I
    identity = np.eye(n)
    residual = np.max(np.abs(solver.M @ solver.L - identity))
    assert residual < 1e-10, f"Leontief multiplier matrix residual too high: {residual}"

    # Hawkins-Simon condition: all multipliers in L must be non-negative
    assert np.all(solver.L >= -1e-9), "Hawkins-Simon violation: negative multipliers detected"

    # Diagonal of L must be at least 1.0 (producing 1 unit requires at least 1 unit gross)
    assert np.all(np.diag(solver.L) >= 1.0)


def test_bike_mounted_machine_gun_bom():
    """Test resolution of 'bike-mounted machine gun' (00MS 'Stinger') down to Salvage."""
    solver = get_economy_solver()

    # Exact alias resolution
    canonical = solver.resolve_item_name("bike-mounted machine gun")
    assert canonical == "00MS “Stinger”"

    # Solve BOM
    plan = solver.solve({"bike-mounted machine gun": 1.0})
    assert plan.resolved_demand == {"00MS “Stinger”": 1.0}

    # 1 Stinger = 1 Caster + 5 Construction Materials
    # Caster = 85 Bmats = 170 Salvage
    # 5 Cmat = 50 Salvage
    # Total Salvage = 220
    assert plan.raw_resources["Salvage"] == 220.0
    assert plan.refined_materials["Basic Materials"] == 85.0
    assert plan.facility_materials["Construction Materials"] == 5.0
    assert plan.intermediate_goods["03MM “Caster”"] == 1.0
    assert plan.gross_production["00MS “Stinger”"] == 1.0
    assert plan.gross_production["Salvage"] == 220.0


def test_alias_variations():
    """Verify alias and fuzzy matching variations for weapons and vehicles."""
    solver = get_economy_solver()

    assert solver.resolve_item_name("mg bike") == "00MS “Stinger”"
    assert solver.resolve_item_name("stinger") == "00MS “Stinger”"
    assert solver.resolve_item_name("00ms") == "00MS “Stinger”"
    assert solver.resolve_item_name("bmats") == "Basic Materials"
    assert solver.resolve_item_name("rmats") == "Refined Materials"
    assert solver.resolve_item_name("cmats") == "Construction Materials"
    assert solver.resolve_item_name("pcmats") == "Processed Construction Materials"
    assert solver.resolve_item_name("spatha") == "Spatha"
    assert solver.resolve_item_name("falchion") == "Falchion"
    assert solver.resolve_item_name("chieftain") == "Silverhand Chieftain - Mk. VI"
    assert solver.resolve_item_name("lordscar") == "Silverhand Lordscar - Mk. X"
    assert solver.resolve_item_name("loughcaster") == "No.2 Loughcaster"
    assert solver.resolve_item_name("storm cannon") == "Storm Cannon"


def test_spatha_facility_bom():
    """Verify multi-tier facility tank BOM (Spatha = Falchion + PCmat + AM IV)."""
    solver = get_economy_solver()
    plan = solver.solve({"Spatha": 1.0})

    assert plan.resolved_demand == {"Spatha": 1.0}
    assert plan.intermediate_goods["Falchion"] == 1.0
    assert plan.refined_materials["Refined Materials"] == 165.0
    assert plan.facility_materials["Processed Construction Materials"] == 35.0
    assert plan.facility_materials["Construction Materials"] == 35.0
    assert plan.facility_materials["Assembly Materials IV"] == 15.0

    # Components: 165 Rmats * 20 = 3300 + 35 PCmat * 3 = 105 -> 3405 Components
    assert plan.raw_resources["Components"] == 3405.0
    # Salvage: 35 Cmat * 10 = 350 Salvage
    assert plan.raw_resources["Salvage"] == 350.0


def test_multi_target_demand():
    """Verify linear combination of joint demands (10 rifles + 5 Stingers)."""
    solver = get_economy_solver()
    plan = solver.solve(
        {
            "No.2 Loughcaster": 10.0,
            "bike-mounted machine gun": 5.0,
        }
    )

    # Loughcaster: 10 * 5 Bmat = 50 Bmat -> 100 Salvage
    # Stinger: 5 * 85 Bmat = 425 Bmat, 5 * 5 Cmat = 25 Cmat -> 850 + 250 = 1100 Salvage
    # Total Bmat = 475, Total Cmat = 25, Total Salvage = 1200
    assert plan.refined_materials["Basic Materials"] == 475.0
    assert plan.facility_materials["Construction Materials"] == 25.0
    assert plan.raw_resources["Salvage"] == 1200.0


def test_machine_count_calculations():
    """Verify physical machine building counts under time constraints."""
    solver = get_economy_solver()
    plan = solver.solve(
        {"00MS “Stinger”": 10.0},
        include_machine_counts=True,
        time_window_seconds=1800.0,  # 30 minutes
    )

    assert plan.machines is not None
    assert "00MS “Stinger”" in plan.machines
    stinger_mach = plan.machines["00MS “Stinger”"]
    assert stinger_mach.facility_type == "Small Assembly Station"
    assert stinger_mach.integer_machines == 1
    assert round(stinger_mach.fractional_machines, 3) == 1.0


def test_server_calculate_required_resources_tool():
    """Test MCP server tool invocation for calculate_required_resources."""
    res_json = calculate_required_resources(demand={"bike-mounted machine gun": 2.0})
    data = json.loads(res_json)

    assert "resolved_demand" in data
    assert data["resolved_demand"]["00MS “Stinger”"] == 2.0
    assert data["raw_resources"]["Salvage"] == 440.0
    assert data["refined_materials"]["Basic Materials"] == 170.0
    assert data["facility_materials"]["Construction Materials"] == 10.0


def test_unknown_item_error():
    """Verify meaningful error response when requesting an unresolvable item."""
    solver = get_economy_solver()
    with pytest.raises(ValueError, match="Could not resolve item 'Spaceship'"):
        solver.solve({"Spaceship": 1.0})
