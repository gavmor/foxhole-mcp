"""Story 2 — recipe matrix from wiki Production rows. OWNER: story-2."""

from __future__ import annotations

import numpy as np

from foxhole.lp.model import EXTRACTION_SOURCES, POWER, Recipe, RecipeMatrix


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
    from foxhole.lp.power import POWER_OUTPUT, consumed_per_run, produced_per_run

    recipes: list[Recipe] = []
    seen_ids: set[str] = set()

    for row in rows:
        output = row.get("Output", "").strip()
        if not output:
            continue

        source = row.get("Source", "").strip()
        is_extraction = source in EXTRACTION_SOURCES

        if is_extraction and not include_extraction:
            continue

        rank_str = row.get("RecipeRank", "1").strip()
        rank = int(rank_str) if rank_str else 1

        base_id = f"{output}#{rank}@{source}"
        rid = base_id
        suffix = 1
        while rid in seen_ids:
            suffix += 1
            rid = f"{base_id}_{suffix}"
        seen_ids.add(rid)

        is_power_plant = output == POWER_OUTPUT

        out_amount_str = row.get("OutputAmount", "").strip()
        out_amount = float(out_amount_str) if out_amount_str else 1.0
        is_crate = row.get("IsCrateOutput", "") == "1"
        crate_cap_str = row.get("CrateCapacity", "").strip()
        crate_cap = int(crate_cap_str) if crate_cap_str else None

        primary_amount = out_amount * crate_cap if (is_crate and crate_cap) else out_amount

        if include_power and is_power_plant:
            outputs: dict[str, float] = {POWER: produced_per_run(row)}
        else:
            outputs = {output: primary_amount}

        sec_out = row.get("SecondaryOutput", "").strip()
        if sec_out:
            sec_str = row.get("SecondaryOutputAmount", "").strip()
            outputs[sec_out] = float(sec_str) if sec_str else 1.0

        ter_out = row.get("TertiaryOutput", "").strip()
        if ter_out:
            ter_str = row.get("TertiaryOutputAmount", "").strip()
            outputs[ter_out] = float(ter_str) if ter_str else 1.0

        inputs: dict[str, float] = {}
        for i in range(1, 7):
            item = row.get(f"InputItem{i}", "").strip()
            if not item:
                continue
            amt_str = row.get(f"InputItem{i}Amount", "").strip()
            inputs[item] = inputs.get(item, 0.0) + (float(amt_str) if amt_str else 1.0)

        vehicle = row.get("InputVehicle", "").strip()
        if vehicle:
            inputs[vehicle] = inputs.get(vehicle, 0.0) + 1.0

        power_mw_str = row.get("InputPower", "").strip()
        power_mw = float(power_mw_str) if power_mw_str else 0.0

        if include_power:
            power_consumed = consumed_per_run(row)
            if power_consumed > 0:
                inputs[POWER] = inputs.get(POWER, 0.0) + power_consumed

        pt_str = row.get("ProductionTime", "").strip()
        seconds = float(pt_str) if pt_str else None

        recipes.append(
            Recipe(
                id=rid,
                source=source,
                rank=rank,
                outputs=outputs,
                inputs=inputs,
                seconds=seconds,
                power_mw=power_mw,
                crate_output=is_crate,
                # Only crate recipes have a crate size (liquids carry CrateCapacity 1 too)
                crate_size=crate_cap if is_crate else None,
                extraction=is_extraction,
            )
        )

    item_set: set[str] = set()
    for recipe in recipes:
        item_set.update(recipe.outputs)
        item_set.update(recipe.inputs)
    items = sorted(item_set)
    item_idx = {name: i for i, name in enumerate(items)}

    n_items = len(items)
    n_recipes = len(recipes)
    A = np.zeros((n_items, n_recipes), dtype=np.float64)
    for j, recipe in enumerate(recipes):
        for item, amount in recipe.outputs.items():
            A[item_idx[item], j] += amount
        for item, amount in recipe.inputs.items():
            A[item_idx[item], j] -= amount

    return RecipeMatrix(items=items, recipes=recipes, A=A)
