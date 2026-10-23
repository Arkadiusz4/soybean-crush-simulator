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

For each month:

1. Give every registered agent the same frozen starting snapshot in ID order.
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

Frozen `MonthlyResult` contains `month`, `starting_state`, `decisions`,
`settlement_results`, and `ending_state`. It checks dates, unchanged monthly
prices, and one settlement per decision. Frozen `SimulationResult` contains
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

Price **values and original source labels are held constant**. Each new month has
new `CommodityPrice` records dated to that simulation month. These are model inputs,
not additional observations from the historical source. There is no interpolation,
historical price replay, or feedback from supply and demand to prices.

No harvesting, new production, imports, or trading settlement replenish soybean
stocks. Purchases and sale offers remain wholly unexecuted. Processing consumes
shared regional stock under the [existing settlement simplification](settlement.md),
not agent ownership or matched Producer-to-Crusher transactions. Repeated batches
within one month, replay protection, and trade matching remain outside this API.

The engine uses no randomness or seed. `SimulationConfig` is not automatically
connected to execution; a caller may explicitly pass `months=config.duration_steps`.
Scenario labels and the stored seed do not activate shocks or stochastic behavior.
