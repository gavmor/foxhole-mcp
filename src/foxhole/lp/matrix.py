"""Story 2 — recipe matrix from wiki Production rows. OWNER: story-2."""

from __future__ import annotations

from foxhole.lp.model import RecipeMatrix


def build_recipe_matrix(
    rows: list[dict[str, str]],
    *,
    include_extraction: bool = False,
    include_power: bool = True,
) -> RecipeMatrix:
    """Build the items x recipes matrix from raw Production rows (ALL RecipeRanks).

    - One Recipe per row; id "<Output>#<RecipeRank>@<Source>" (deduplicate with a suffix if
      two rows collide).
    - Per-run outputs: OutputAmount * CrateCapacity when IsCrateOutput == "1", else
      OutputAmount (blank -> 1). Byproducts: SecondaryOutput/SecondaryOutputAmount and
      TertiaryOutput/TertiaryOutputAmount (same crate rule NOT applied to byproducts).
    - Per-run inputs: InputItem1..6 with Amounts; InputVehicle counts 1.
    - include_power: add POWER to inputs via foxhole.lp.power.consumed_per_run(row) and to
      outputs of power-plant rows via produced_per_run(row) (rows with Output "Facility
      Power" then output POWER instead of "Facility Power").
    - include_extraction=False drops rows whose Source is in EXTRACTION_SOURCES (their
      outputs become raw items). True keeps them with extraction=True.
    - Rows with no Output are skipped. A = outputs - inputs per run.
    """
    raise NotImplementedError("story-2")
