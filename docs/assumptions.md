# Modeling assumptions

These assumptions define the intended first model. Input preparation and a
price-responsive producer supply heuristic are implemented; market execution
and the monthly engine are not.

## Initial scope

- **Regions:** United States, Brazil, Argentina, and European Union. The software
  allows any nonempty subset of these four regions. The treatment of trade with
  regions outside the model is still open.
- **Commodities:** soybeans, soybean meal, and soybean oil.
- **Time:** one simulation step will represent one calendar month. The current
  `duration_steps` field validates the number of steps; it does not execute time
  progression or assign a start date.
- **Purpose:** compare simplified disruption scenarios, rather than provide
  accurate market or price forecasts.

## Planned economic behavior

Producers currently offer a configurable share of existing regional soybean
inventory in response to the global market/reference price ratio. Inventory is
treated as available supply for the decision; no crop growth, harvesting, new
production, or stock deduction occurs. See the
[producer heuristic](architecture.md#producer-supply-heuristic) for its defaults.

Crushers will purchase soybeans and convert them into meal and oil. Traders will
move commodities between regions in response to regional price differences,
available supply, and transportation costs. Their decision rules and the number
of agents per region are not yet chosen.

Supply and demand will influence prices, and processing profitability will inform
crusher decisions. These are intended relationships, not implemented algorithms.
No price formula, yield, elasticity, or cost coefficient is fixed yet.

Historical quantities and reference prices will initialize the model. Prices will
then evolve through model rules and scenario effects, rather than replaying or
forecasting the source price series. Annual or marketing-year flows will be
allocated to months using a simplified rule that preserves their annual totals.
The allocation rule and seasonality remain open; stock measurements require
separate treatment. See [data sources](data-sources.md).

Mass balance, nonnegative feasible inventories, and capacity limits are intended
future invariants. Input models already reject negative or nonfinite quantities,
but no market engine enforces state transitions yet. Raw historical balances are
not required to satisfy an exact accounting equation.

## Initial inventory reference

The implemented inventory builder uses USDA `beginning_stocks_tonnes` as the
initial quantity in metric tonnes. Missing stocks are rejected, known zero stocks
are preserved, and ending stocks are not used as a substitute.

These are annual marketing-year reference values representing stocks at the
beginning of the selected marketing year. They are not necessarily actual
inventories on the selected calendar price month. The builder does not model
inventory changes between the marketing-year start and the simulation start
month; temporal alignment remains an open modeling decision.

`build_simulation_input()` combines these stocks and annual balances with an
explicitly selected monthly global price. This is a baseline initialization
assumption, not a historical reconstruction of a specific calendar date.

## Existing configuration conventions

`duration_steps` is a positive integer and `random_seed` is a nonnegative integer.
`regions` uses unique identifiers from `Region`; `scenario_name` is a trimmed,
nonempty label. All four fields are required, extra fields are rejected, and
validated settings are immutable.

The seed is only stored and validated today; simulation reproducibility is not
implemented. Likewise, `baseline` is an example label, not an implemented scenario.
Validation accepts other names without checking a scenario registry.

## Open Design Decisions

| Topic | Decisions still required |
| --- | --- |
| Price adjustment | Exact formula, supply and demand response, bounds, and treatment of unmet demand |
| Crushing | Meal and oil yield coefficients, processing costs, capacity, losses, and treatment of by-products |
| Agents | Number per region, crusher/trader rules, producer response calibration, information available, expectations, and decision ordering |
| Trade | Route capacity, transportation costs, lead times, feasibility, and trade outside the four regions |
| Production timing | Seasonality, harvest months, and monthly allocation of annual quantities |
| Scenarios | Drought severity, demand increases, transportation cost shocks, duration, and timing |
| Historical initialization | Source releases, start month, regional mapping, temporal alignment of initial stocks, and regional price differences |
| Accounting and results | Physical units, currency and inflation treatment, storage costs, and monthly result structure |
| Randomness and evaluation | Stochastic processes, reproducibility guarantees, and validation metrics |

Monthly steps and the initial region and commodity scope are agreed for this
stage. The economic choices above must be documented and tested as they are made.
