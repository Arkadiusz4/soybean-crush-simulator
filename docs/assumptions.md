# Modeling assumptions

These assumptions define the intended first model. Input preparation and basic
Producer, Crusher, and Trader heuristics, physical crushing settlement, and a
deterministic monthly engine are implemented; trade execution is not.

## Initial scope

- **Regions:** United States, Brazil, Argentina, and European Union. The software
  allows any nonempty subset of these four regions. The treatment of trade with
  regions outside the model is still open.
- **Commodities:** soybeans, soybean meal, and soybean oil.
- **Time:** one engine step represents one calendar month, identified by its first
  day. The initial state supplies the starting month. The engine's `months` argument
  is explicit; `duration_steps` remains a validated configuration value that callers
  can pass to it.
- **Purpose:** compare simplified disruption scenarios, rather than provide
  accurate market or price forecasts.

## Planned economic behavior

Producers currently offer a configurable share of existing regional soybean
inventory in response to the global market/reference price ratio. Inventory is
treated as available supply for the decision; no crop growth, harvesting, new
production, or stock deduction occurs. See the
[producer heuristic](architecture.md#producer-supply-heuristic) for its defaults.

One aggregate Crusher per region currently requests processing from net margin,
configured monthly input capacity, and regional soybean stocks. The default
80% meal / 20% oil yields are simplified assumptions with no losses or by-products.
Regional stocks are a request ceiling, not owned or reserved crusher inventory;
Producer offers and Crusher requests may refer to the same stock. Settlement
allocates CRUSH requests by agent ID against remaining regional stocks and applies
configured yields, trusting agent capacity decisions. This is direct physical
processing, not a real trade from Producer to Crusher. Trade matching and ownership
remain future work. See [physical settlement](settlement.md) and the
[crusher heuristic](architecture.md#crusher-processing-heuristic) for the formulas.

Traders currently request soybean purchases and offer regional meal and oil
stocks based on global market/reference price ratios. Purchase requests represent
demand independently of current soybean inventory. Product stocks are shared
regional availability proxies, not owned or reserved trader stocks. No transfers,
cross-border trade, routes, transport costs, counterparties, or budgets are modeled.
Future market clearing must prevent double-counting across all agents. The number
of producers and traders per region remains open. See the
[trader heuristics](architecture.md#trader-purchase-and-sale-heuristics).

Supply and demand will influence prices through rules that remain undecided.
The current producer, crusher, and trader response parameters are modeling defaults;
price formulas, elasticities, and empirical parameter calibration remain open.

Historical quantities and reference prices initialize the baseline. The current
engine holds price values and source labels constant while updating observation
dates to each simulation month. Dynamic price adjustment remains planned. No
annual or marketing-year flows are allocated to months, so new production, imports,
and consumption flows do not replenish or deplete inventories. The allocation rule
and seasonality remain open; stock measurements require separate treatment.
See [data sources](data-sources.md) and [the engine assumptions](simulation.md#current-assumptions-and-limitations).

Physical settlement enforces nonnegative finite stocks and consumes each soybean
tonne at most once within a batch. Meal and oil output follow the configured
yields and their numerical tolerance. Agent-owned inventory, trade accounting,
and capacity guarantees across independently repeated batches remain open.
The engine settles once per month and carries ending stocks into the next month.
Raw historical balances are not required to satisfy an exact accounting equation.

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

The seed is only stored and validated; the deterministic engine uses no randomness.
Likewise, `baseline` is an example label, not an implemented scenario.
Validation accepts other names without checking a scenario registry.

## Open Design Decisions

| Topic | Decisions still required |
| --- | --- |
| Price adjustment | Exact formula, supply and demand response, bounds, and treatment of unmet demand |
| Crushing | Calibration of yields, processing costs and capacity; losses and by-products beyond the simplified 80/20 split |
| Agents | Producer/trader counts, response calibration, information available, expectations, and decision ordering |
| Trade | Route capacity, transportation costs, lead times, feasibility, and trade outside the four regions |
| Production timing | Seasonality, harvest months, and monthly allocation of annual quantities |
| Scenarios | Drought severity, demand increases, transportation cost shocks, duration, and timing |
| Historical initialization | Source releases, start month, regional mapping, temporal alignment of initial stocks, and regional price differences |
| Accounting and results | Physical units, currency and inflation treatment, storage costs, and monthly result structure |
| Randomness and evaluation | Stochastic processes, reproducibility guarantees, and validation metrics |

Monthly steps and the initial region and commodity scope are agreed for this
stage. The economic choices above must be documented and tested as they are made.
