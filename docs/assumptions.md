# Modeling assumptions

The simulator represents the United States, Brazil, Argentina, and the European
Union; commodities are soybeans, soybean meal, and soybean oil. Each step is one
calendar month. Agent decisions are deterministic intentions based on simplified
price and availability rules; regional stocks are shared availability proxies,
not agent-owned or reserved inventory.

Crushing consumes soybeans and records meal, oil, and any configured residual.
Synthetic defaults use an 80% meal / 20% oil split. Historical calibration can
use source production-to-crush ratios and records the remainder as residual;
see [historical yields](historical-market.md#historical-conversion-yields-and-the-brazil-discrepancy).
Processing capacity is a limit, not guaranteed throughput.

The physical cycle accounts for supply, consumption, crushing, and matched
interregional trade. Historical boundary flows can represent trade outside the
modeled regions. Regional prices respond to modeled market pressure, subject to
configured limits; they are not transaction-price observations. Demand-aware
settlement records fulfilled and unmet consumption where enabled. See
[simulation](simulation.md) and [settlement](settlement.md).

## Initial inventory reference

Beginning stocks are marketing-year reference values, not necessarily stocks at
the selected calendar price month. Missing stocks are rejected, while observed
zero stocks are preserved. The builder does not reconstruct changes between the
marketing-year start and the simulation start.

Annual USDA quantities are allocated into modeled monthly flows using assumed
weights, not observed monthly production or consumption. Historical reference
prices initialize the model rather than forecast prices. See [data sources](data-sources.md).

## Open design decisions

Future work may refine harvest seasonality, agent behavior and calibration,
transport costs and routes, stock ownership, by-products, and the alignment of
marketing-year stocks with calendar dates. These simplifications limit economic
interpretation; results are scenarios, not forecasts.
