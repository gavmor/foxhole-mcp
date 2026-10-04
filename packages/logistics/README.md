# logistics

Generic, domain-agnostic logistics algorithms, extracted from
[`foxhole-mcp`](https://github.com/gavmor/foxhole-mcp). Nothing here knows about any
particular game or economy: you supply the recipes and constraints, it plans production.

## What's in it

- **Recipe-matrix LP** (`solve_lp`) — choose how many runs of each recipe plus raw intake
  to hit output targets, minimising weighted raw (`MIN_RAW`), weighted effort
  (`MIN_MINING_TIME`), or maximising throughput (`MAX_THROUGHPUT`), in quantity or rate mode.
- **Recipe-matrix MILP** (`solve_milp`) — same model with whole runs, integer facility
  counts, and facility caps.
- **Leontief input-output** (`solve_leontief`) — solve `(I − A)x = d` for gross production
  rates, with Hawkins-Simon viability checks and optional machine-count sizing.
- **Hauling** (`trips_needed`, `trip_constraint`, `trips_for_mixed_load`) — slot-pack raw
  resources into fixed-capacity vehicles described by `(slots, stack)`.

## Domain parameters

The package ships with no baked-in domain data. Callers inject what used to be hard-coded:

- `Limits.produced_only` — items that may never be taken as raw intake (must be produced),
  e.g. an energy commodity.
- `Limits.effort_weights` — per-raw-item weights for the `MIN_MINING_TIME` objective
  (e.g. hand-gathering seconds per unit).
- Hauling functions take an explicit `(slots, stack)` vehicle spec and `hauled_items` set.

## Example

```python
import numpy as np
from logistics import Recipe, RecipeMatrix, Target, solve_lp

recipes = [
    Recipe(id="widget#1@shop", source="shop", rank=1, outputs={"widget": 1}, inputs={"ore": 10}),
]
items = ["ore", "widget"]
A = np.array([[-10.0], [1.0]])  # rows = items, cols = recipes
matrix = RecipeMatrix(items=items, recipes=recipes, A=A)

result = solve_lp(matrix, [Target(item="widget", quantity=5)])
print(result.runs, result.raw_used)  # {'widget#1@shop': 5.0} {'ore': 50.0}
```
