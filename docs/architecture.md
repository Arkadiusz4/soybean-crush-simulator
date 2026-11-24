# Architecture

The installable package is `soybean_sim` under `src/`. The CLI validates YAML
configuration and reports the package version. The dashboard consumes completed
results through its adapter; simulation logic does not depend on Streamlit.

| Component | Responsibility |
| --- | --- |
| `models/` | Shared enums, market observations, and simulation inputs |
| `data/` | Local USDA and World Bank loading, selection, and input assembly |
| `agents/` | Producer, Crusher, Trader, and shared decision contracts |
| `market/` | Inventories, flows, settlement, trade, and regional pricing |
| `simulation/` | Monthly orchestration and immutable result history |
| `scenarios/` | Experiments, comparisons, and historical preparation |
| `dashboard/` | Run controls and result visualization |

## Market input models

`MarketBalance` retains annual USDA marketing-year quantities in metric tonnes;
`CommodityPrice` contains a monthly global USD-per-tonne reference. `Inventory`
represents regional stocks. `SimulationInput` combines selected records with
inventories initialized from beginning stocks. See [data sources](data-sources.md)
for validation and [assumptions](assumptions.md#initial-inventory-reference) for
the timing limitation.

Agents read an immutable `MarketSnapshot` and return intentions. The engine
settles physical activity and records results, carrying inventories forward and
updating regional prices when configured. Agent decisions alone do not execute
transactions. The [simulation](simulation.md) and [settlement](settlement.md)
references describe execution order, accounting, and Python APIs.

Synthetic and historical runners prepare inputs separately from execution.
[Scenarios](scenarios.md) describes paired comparisons, while [historical
integration](historical-market.md) documents calibration and external trade.
