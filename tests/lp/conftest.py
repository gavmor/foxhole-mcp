"""Shared LP fixtures: 60 real wiki Production rows (23 outputs, 8 with byproducts)."""

import json
from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data" / "production_subset.json"


@pytest.fixture
def production_rows() -> list[dict[str, str]]:
    return json.loads(DATA.read_text())
