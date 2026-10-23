# Market state and initial settlement

The `soybean_sim.market` API provides `MarketState`, `SettlementResult`,
`SettlementOutcome`, `SettlementError`, and `settle_decisions()`.

`MarketState` stores one frozen `MarketSnapshot` in `state.snapshot`, with no
parallel inventory store or annual flows. `MarketState.from_snapshot(snapshot)`
validates the initial observations, including nested prices and stocks. Agents
can observe `state.snapshot`; settlement returns a new state rather than changing
that observation. The month and global reference prices stay unchanged.

## Requested versus executed quantities

Each frozen `SettlementResult` contains the original `decision`,
`requested_tonnes`, `executed_tonnes`, `unfulfilled_tonnes`, and an `outcome`.
Quantities are finite and nonnegative. The requested quantity exactly matches
the decision; execution cannot exceed it. Requested tonnes must equal executed
plus unfulfilled tonnes within relative tolerance `1e-12` and absolute tolerance
`1e-9` tonnes. Tolerance never permits overexecution.

`CRUSH_SETTLED` means the physical request was evaluated; it may execute fully,
partly, or zero tonnes. The quantities show which occurred.
`TRADE_MATCHING_NOT_IMPLEMENTED` means a purchase or sale remains entirely
unexecuted: executed tonnes are zero and unfulfilled tonnes equal the request.
Offers do not reserve stock or establish a Producer-to-Crusher transaction.

## Deterministic physical processing

`settle_decisions(state, decisions, crusher_configs=...)` returns
`tuple[MarketState, tuple[SettlementResult, ...]]`:

1. Validate all decisions and referenced `CrusherConfig` objects before processing.
   Every decision must use the current month and a represented region and
   commodity. Regions are represented by inventory records; commodities may
   appear in prices or inventories. A deferred purchase does not require a
   regional stock record for the commodity it requests.
2. Require consistent identities for repeated agent IDs. A crusher ID may have
   only one CRUSH decision per batch. Different crushers may share a region.
3. Sort by case-sensitive agent ID, action identifier, commodity identifier, and
   requested tonnes. Process in that order and return results in the same order,
   independently of submission order. This gives lower crusher IDs priority.
4. For each CRUSH, require a crusher-role identity, soybeans as input, a config
   keyed by the agent ID, and explicit regional soybean, meal, and oil records.
   Missing output stocks are errors even for zero requests; no records are invented.
5. Execute `min(requested_tonnes, remaining_regional_soybeans)`. Subtract that
   input, then add `executed_tonnes * meal_yield_fraction` and
   `executed_tonnes * oil_yield_fraction` to existing product stocks.

Working quantities are local to the call. Invalid batches raise `SettlementError`;
even if an overflow is detected after earlier local processing, the supplied
state is unchanged and no partial state is returned. Resulting inventory records
are sorted by region and commodity. Negative or nonfinite results are errors;
no large invalid quantities are clipped.

Settlement trusts the requested quantity regarding monthly capacity and does
not repeat margin or utilization calculations. It reuses `CrusherConfig` yield
validation: nonnegative finite coefficients summing to one within absolute
tolerance `1e-9`. Coefficients are used as supplied, without normalization, so
mass differences follow that yield tolerance and floating-point rounding. The
default 80/20 split is the existing simplified assumption.

## Synthetic example

This example uses illustrative quantities and prices, not historical data:

```python
from datetime import date

from soybean_sim.agents import AgentIdentity, AgentType, Crusher, CrusherConfig
from soybean_sim.market import MarketState, settle_decisions
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketSnapshot,
    Region,
)

month = date(2021, 1, 1)
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
                data_source="synthetic example",
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
        agent_id="crusher-br-1",
        agent_type=AgentType.CRUSHER,
        region=Region.BRAZIL,
    ),
    config=CrusherConfig(
        monthly_capacity_tonnes=6000,
        processing_cost_usd_per_tonne=10,
    ),
)
updated, results = settle_decisions(
    state,
    crusher.decide(state.snapshot),
    crusher_configs={crusher.identity.agent_id: crusher.config},
)
```

The new Brazilian stocks are 4,000 tonnes soybeans, 4,800 tonnes meal, and 1,200
tonnes oil. The single result records 6,000 executed input tonnes and zero
unfulfilled tonnes. The initial state still has its original stocks.

## Current limitations

Processing directly against shared regional stocks is a temporary modeling
simplification, not ownership, a reservation, or a real purchase from producers.
Sequential execution prevents crushers consuming the same soybean tonnes within
one batch. The [monthly engine](simulation.md) carries returned stocks forward
and advances the calendar, while direct settlement callers remain responsible
for carrying their returned state. Batch replay prevention, trade matching, and
allocation across actual transactions remain future work. Global reference prices
are unchanged; no
regional transaction prices, counterparties, budgets, routes, or shipments exist.

When initializing from historical inputs, the existing
[beginning-stocks timing assumption](assumptions.md#initial-inventory-reference)
still applies. No annual-to-monthly allocation or calendar reconstruction occurs.
