# Monthly simulation engine

The Python API in `soybean_sim.simulation` exports `run_simulation`, `next_month`,
`MonthlyResult`, `SimulationResult`, and `SimulationError`. Loading historical
data remains separate from execution; no CLI simulation command is added.

## Execution and results

```python
result = run_simulation(initial_state, agents, crusher_configs=configs, months=3)
```

The initial state is the beginning of the first month. The engine revalidates it,
requires a positive integer duration within Python's date range, and registers
unique agent IDs in ascending case-sensitive order. Identities must be valid and
their regions represented by inventory records. Every crusher needs a validated
`CrusherConfig` keyed by its ID, even if it later returns no decisions.

With `monthly_flows=None` (the default), each month uses the legacy cycle:

1. Give agents starting inventories and global or regional price observations in
   ID order.
2. Collect decisions, preserving each agent's own decision order. Require each
   decision to name its registered agent. The existing settlement validates months.
3. Call `settle_decisions()` once with the complete batch. Its allocation policy
   stays unchanged: CRUSH consumes soybeans and adds meal/oil; trades are deferred.
4. Record the starting state, decisions, settlement results, and ending state.
5. If another month is requested, carry ending inventories forward and advance
   the calendar, including December/January and leap-year boundaries.

The engine never changes inventories directly. Empty agent collections still
produce the requested monthly history. Agent and settlement errors propagate;
no partial simulation result is returned and the supplied initial state is unchanged.
Custom agents must honor the deterministic, stable-identity `Agent` contract.

## Physical monthly cycle

Provide `monthly_flows` to opt into physical production, trading, and consumption.
An explicitly empty iterable enables this mode with no external flows:

```python
result = run_simulation(
    initial_state,
    agents,
    crusher_configs=configs,
    months=3,
    regional_prices=regional_prices,
    monthly_flows=flows,
)
```

The caller prepares flows separately; the engine does not load datasets or generate
monthly allocations. The complete collection is materialized, revalidated, checked
for duplicates and supported accounting, and grouped by month before execution.
Every flow must fall within the run's period and reference an existing regional
commodity inventory. Months without records are allowed.

Each physical month follows this order:

1. Open with the previous ending inventories and advance observation dates.
2. Apply soybean SUPPLY. Meal/oil SUPPLY is validated reference data and adds no stock.
3. Collect agent decisions using post-production inventories and global or regional
   price views.
4. Settle only CRUSH decisions, using the existing stock-limited processing policy.
5. Match all trade intentions against post-crushing inventories, then settle the
   matched transfers as one batch.
6. Apply meal/oil CONSUMPTION against the resulting stocks, including imports.
7. Record the ending state for the next month.

Soybean consumption flows are rejected: crushing already accounts for soybean use.
Incoming trades cannot fund sales within the same matching/settlement batch.
Consumption shortages propagate `FlowSettlementError`; consumption is never reduced
or funded through automatic imports. Availability is checked at the execution stage,
so current-month crushing outputs and imports can satisfy product consumption.

Crushing output is available to the matcher in the same month when a corresponding
sale intention exists. Existing agents still decide before crushing; this order
does not make them anticipate future outputs or create extra offers automatically.
No stage failure returns a partial simulation result or changes the input state.

## Regional price observations

With price dynamics disabled, `run_simulation(..., regional_prices=None)` preserves
the original shared global snapshot behavior. Supply an initial-month
`RegionalPrice` series to give each agent the prices for its own region:

```python
result = run_simulation(
    initial_state,
    agents,
    crusher_configs=configs,
    months=3,
    regional_prices=regional_prices,
)
```

`initialize_regional_prices()` can create baseline regional prices from global
observations. The engine validates the complete supplied series before any agent
decides, including records for unused regions. Every agent region needs all
snapshot commodities; regions without agents need no coverage. Invalid data
propagates `RegionalPriceError` without returning partial results.

Each agent receives a temporary `snapshot_for_region()` view with the current
month and **all** regional inventories. Its `CommodityPrice` records use the
`simulated_regional` source label. Stored market states, monthly histories, and
settlement retain global reference prices.

With `price_dynamics_config=None`, regional values stay constant; their dates advance
using the existing calendar utility. Supplied records remain unchanged. In physical
mode, regional views include post-production inventories.

## Dynamic regional pricing

Enable price feedback explicitly with the existing market configuration:

```python
from soybean_sim.market import PriceDynamicsConfig

result = run_simulation(
    initial_state,
    agents,
    crusher_configs=configs,
    months=3,
    monthly_flows=flows,  # An empty tuple also enables physical mode.
    price_dynamics_config=PriceDynamicsConfig(),
)
```

Dynamic pricing requires `monthly_flows` to be provided; `None` raises
`SimulationError` before decisions run. If regional baselines are omitted, the
engine calls `initialize_regional_prices()` using every inventory region,
including regions without agents. Explicit baselines retain their values and must
cover all three commodities in exactly those regions. Invalid settings or coverage
fail before execution with the existing pricing or regional-price domain error.

Agents observe the current month's regional values. After the physical cycle,
`calculate_market_pressures()` receives that month's flows, executed trades, and
executed crushing totals. Inputs are summed with `math.fsum()`; meal/oil outputs
use each settled crusher's configured yields. Requested or unmatched intentions
do not contribute, and reference-only USDA meal/oil supply remains excluded.

`update_regional_prices()` computes next month's values from those pressures.
The engine uses its next-month dates directly, including December-to-January
transitions. Updated prices never replace observations within the completed
month. Global reference values and source labels remain unchanged. A single-month
run still returns exactly one result; computed following-month prices are not
stored as an extra month. The updater propagates its calendar error if no following
month can be represented, including a dynamic run ending in December 9999.

## Monthly history

Frozen `MonthlyResult` contains `month`, `starting_state`, `decisions`,
`settlement_results`, and `ending_state`. The starting state is the opening state
before production; agent views in physical mode include that month's production.
The additional fields default to `physical_cycle=False`, `market_flows=()`, and
`executed_trades=()` for legacy callers. The mode flag distinguishes an explicitly
empty physical cycle from legacy execution.

`regional_prices=()` and `market_pressures=()` are additional empty defaults.
Regional history stores the values observed during the recorded month, including
constant regional runs. Pressures are recorded only when dynamic pricing is
enabled. Each collection requires unique region/commodity keys and dates matching
the result month; legacy records cannot contain pressures. Following-month prices
are recorded only when that month is actually simulated.

Physical months retain every decision, every validated flow (including reference
production), crushing settlement results only, and actually matched/settled trades.
Unmatched trade intentions remain in `decisions` without deferred settlement
records. Legacy months retain one settlement per decision as before. Both modes
check dates and unchanged global price observations. Frozen `SimulationResult` contains
`initial_state`, nonempty `monthly_results`, and `final_state`, validating connected
months and carried stocks and prices. The final state is the last completed
month's ending state; it is not advanced into an extra month.

## Initialization from prepared data

Given `initial: SimulationInput` returned by the
[data builder](data-sources.md#building-simulation-inputs):

```python
from soybean_sim.market import MarketState
from soybean_sim.models import MarketSnapshot

snapshot = MarketSnapshot(
    month=initial.prices[0].month,
    prices=initial.prices,
    inventories=initial.inventories,
)
state = MarketState.from_snapshot(snapshot)
```

`SimulationInput` already requires nonempty prices sharing one month. Annual USDA
balances are deliberately excluded from the snapshot; no production or consumption
flow is divided into months. The existing
[beginning-stocks assumption](assumptions.md#initial-inventory-reference) still applies.
Supply all records needed by the chosen agents and settlement, including explicit
meal/oil inventories for crushing. Missing stocks are not manufactured as zero.

## Runnable synthetic example

This self-contained example uses illustrative inputs, not historical observations:

```python
from datetime import date

from soybean_sim.agents import AgentIdentity, AgentType, Crusher, CrusherConfig
from soybean_sim.market import MarketState
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketSnapshot,
    Region,
)
from soybean_sim.simulation import run_simulation

month = date(2023, 11, 1)
prices = {
    Commodity.SOYBEANS: 250,
    Commodity.SOYBEAN_MEAL: 300,
    Commodity.SOYBEAN_OIL: 800,
}
stocks = {
    Commodity.SOYBEANS: 10000,
    Commodity.SOYBEAN_MEAL: 0,
    Commodity.SOYBEAN_OIL: 0,
}
state = MarketState.from_snapshot(
    MarketSnapshot(
        month=month,
        prices=tuple(
            CommodityPrice(
                commodity=commodity,
                month=month,
                price_usd_per_tonne=price,
                data_source="synthetic monthly example",
            )
            for commodity, price in prices.items()
        ),
        inventories=tuple(
            Inventory(
                region=Region.BRAZIL,
                commodity=commodity,
                quantity_tonnes=quantity,
            )
            for commodity, quantity in stocks.items()
        ),
    )
)
crusher = Crusher(
    identity=AgentIdentity(
        agent_id="crusher-br",
        agent_type=AgentType.CRUSHER,
        region=Region.BRAZIL,
    ),
    config=CrusherConfig(
        monthly_capacity_tonnes=4000, processing_cost_usd_per_tonne=10
    ),
)
result = run_simulation(
    state,
    [crusher],
    crusher_configs={crusher.identity.agent_id: crusher.config},
    months=4,
)
executed = [
    sum(item.executed_tonnes for item in monthly.settlement_results)
    for monthly in result.monthly_results
]
assert executed == [4000, 4000, 2000, 0]
assert result.final_state.snapshot.month == date(2024, 2, 1)
```

## Current assumptions and limitations

Global reference **values and original source labels are held constant**. Each new
month has new `CommodityPrice` records dated to that simulation month. These are
model inputs, not additional observations from the historical source. There is no
interpolation or historical price replay. Regional price feedback is an optional, uncalibrated
heuristic using the existing sensitivity, monthly cap, price floor, and supply floor.

Legacy runs do not replenish stocks through production or imports, and their
purchase/sale intentions remain wholly unexecuted. Physical runs apply caller-supplied
flows and matched transfers using shared regional stock, without agent ownership
or reservation. Neither mode models harvesting or derives new flow data inside the
engine. Repeated-batch replay protection, scenarios, and empirical calibration
remain outside this API. Opening stock levels and unmatched intentions are not
additional pressure signals; only the existing physical-activity accounting is used.

The existing `scripts/smoke_test_simulation.py` omits `monthly_flows` and exercises
legacy mode. The in-memory physical-cycle integration test separately covers
production, crushing, same-month product trades, and consumption across two regions.
The dynamic-pricing integration test separately checks observed next-month values,
configured crushing, physical flows, and pressure history across a year boundary.

The engine uses no randomness or seed. `SimulationConfig` is not automatically
connected to execution; a caller may explicitly pass `months=config.duration_steps`.
Scenario labels and the stored seed do not activate shocks or stochastic behavior.
