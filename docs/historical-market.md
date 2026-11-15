# Historical physical-market integration

The `soybean_sim.scenarios` API connects the existing USDA and World Bank loaders
to the physical engine. It preserves original quantities and either returns a
complete history or raises `HistoricalMarketError` with inspectable diagnostics.
It does not calibrate the market model to historical outcomes.

## Inputs and time assumptions

The default selection uses all four regions and all three commodities:

- USDA marketing-year label `2024` from `data/raw/psd_oilseeds.csv`.
- World Bank reference observations for `2025-01-01` from
  `data/raw/CMO-Historical-Data-Monthly.xlsx`.
- Simulation calendar months January through December 2025.

See [data sources](data-sources.md) for manual dataset placement. No files are
downloaded or replaced with fixtures. Unavailable periods and missing required
observations block preparation.

The marketing-year label and calendar month are selected independently. Opening
inventories use USDA beginning stocks unchanged. Those stocks describe the start
of the marketing year; treating them as available in January is an explicit
initialization assumption, not a reconstruction of January 1 inventories.

`MonthlyFlowConfig` allocates annual quantities using the existing generator;
the default weights are `1/12` in every calendar month. These are **modeled
allocations**, not observed monthly USDA data. Preparation requires production
for every selected balance and domestic consumption for meal and oil. Missing
values remain errors rather than becoming zero. Known zero values are retained.

Soybean production supplies physical inventory. Meal/oil production remains
reference-only: physical products come from executed crushing. Soybean domestic
consumption is excluded to avoid counting crushing inputs twice. The smoke script
also enables explicit boundary trade; annual imports/exports are allocated
uniformly as described below, not silently added as opening inventory.

January's global reference values remain constant throughout execution; the
engine advances their observation labels for agent snapshots. The original
World Bank observation month is retained separately in metadata. Regional prices
are simulated values, not a replay of later historical prices.

## Preparation, screening, and execution

```python
from soybean_sim.agents import TraderConfig
from soybean_sim.market import PriceDynamicsConfig
from soybean_sim.models import Commodity, Region
from soybean_sim.scenarios import (
    calibrate_historical_crushers,
    load_historical_market,
    preflight_historical_market,
    run_historical_market,
)

prepared = load_historical_market(
    "data/raw/psd_oilseeds.csv",
    "data/raw/CMO-Historical-Data-Monthly.xlsx",
    include_historical_trade=True,
)
references = {
    item.commodity: item.price_usd_per_tonne for item in prepared.reference_prices
}
crushers = calibrate_historical_crushers(
    prepared, processing_cost_usd_per_tonne=35, calibrate_yields=True
)
traders = {
    region: TraderConfig(
        reference_soybean_price_usd_per_tonne=references[Commodity.SOYBEANS],
        reference_soybean_meal_price_usd_per_tonne=references[Commodity.SOYBEAN_MEAL],
        reference_soybean_oil_price_usd_per_tonne=references[Commodity.SOYBEAN_OIL],
        maximum_monthly_purchase_tonnes=5_000,
    )
    for region in Region
}
settings = dict(
    crusher_configs=crushers,
    trader_configs=traders,
    price_dynamics_config=PriceDynamicsConfig(),
)
screen = preflight_historical_market(prepared, **settings)
if screen.can_run:
    result = run_historical_market(prepared, **settings)
else:
    print("Blocked:", *screen.blocking_errors, sep="\n")
```

`prepare_historical_market()` accepts existing in-memory loader records instead
of file paths. Both preparation APIs retain selected annual balances, original
reference prices, initial state, modeled flows, and frozen metadata. File loading
also records absolute source paths and SHA-256 digests for reproducibility.
`include_historical_trade=True` enables the trade schedule in either API; the
default remains False for compatibility with earlier reference-only callers.
Missing annual imports or exports block preparation when enabled. Explicit zero
observations produce zero transfers rather than being inferred from missing data.

## Explicit physical trade and the external boundary

`ExternalTradeFlow`, exported from `soybean_sim.market`, records a month, commodity,
quantity, source, and destination. Exactly one endpoint is a supported region and
the other is `ExternalMarket.CLEARING_BOUNDARY`. Each annual USDA import/export
quantity contributes `annual_tonnes / 12` to every requested calendar month,
independent of production weights or simulation duration. These are **modeled
monthly allocations**, not observed monthly trade.

PSD totals do not identify bilateral counterparties, and the four regions do not
cover the world. The boundary is an unmodeled clearing market for gross trade,
not a claim that all imports originated outside the four regions. No bilateral
routes or outside starting inventories are invented. Regional imports debit the
external mass ledger; regional exports credit it. The signed external change is
`exports - imports`, exactly opposite to the regional boundary inventory change.
External availability follows the exogenous schedule; finite outside stocks are
not simulated. A negative external ledger change is not an invented negative stock.

Historical boundary mode replaces agent trade matching: offers and purchase
intentions remain recorded, but are not settled again on top of USDA gross totals.
Ordinary runs without a boundary schedule retain existing interregional matching.

The monthly order is production, boundary imports, agent decisions, crushing,
domestic consumption, and boundary exports. Imports can support January crushing
and consumption. Full exports require remaining physical stock; neither exports
nor consumption are clipped. `MonthlyResult.external_trade_flows` records only
fully settled transfers. Imports contribute supply and exports demand to the
existing price-pressure calculation; the price formula is unchanged.

Paired runs use the identical boundary schedule. Drought changes only new Argentine
soybean supply, leaving historical trade allocations and processing settings fixed.

## Feasibility screening

Preflight checks coverage, finite/nonnegative data, beginning stocks, unique
records, dates, allocation identity, and complete explicit agent configurations.
It rejects altered opening stocks or scaled flows rather than repairing them.

The screen bounds cumulative crushing by both processing capacity and available
soybeans: opening stocks, modeled soybean production, and scheduled imports,
reserving scheduled soybean exports. Product consumption **plus exports** must
fit opening product stocks, scheduled imports, and the output of this crush bound.
The original reference-only mode still uses an optimistic prior-purchase bound.
Diagnostics identify the first certain regional deficit, month, cumulative
quantities, and shortfall. Trade coverage and exact annual/12 quantities are
revalidated, so adding arbitrary transfers cannot bypass the screen.
Passing this optimistic screen does **not** guarantee actual settlement: margins
and soybean availability may reduce processing. Execution failures retain the
last observed decision scenario/month and the original domain error; no partial
history is returned. A failure before decisions may precede that month's observer.

## Demonstration and current limitations

```bash
uv run python scripts/smoke_test_historical_market.py
uv run python scripts/smoke_test_historical_market.py --verbose
uv run python scripts/smoke_test_historical_market.py --compare
```

The script prints source dates, assumptions, selected prices, explicit agent
settings, and separate preparation/preflight/execution statuses. Verbose mode
adds source hashes, annual quantities, weights, and first-month allocations.
Successful runs include independent inventory accounting, crushing/trade totals,
observed regional price changes, and final stocks. Blocking failures exit nonzero.

### Processing scale and remaining infeasibility

The old 10,000 t/month capacities came from a small demonstration and were
incompatible with historical consumption. `calibrate_historical_crushers()` now
sets each monthly input limit to the selected annual USDA `crush_tonnes / 12`,
independent of simulation duration. This is a throughput-based modeling proxy,
not an estimate of installed nameplate capacity. Missing, zero, or invalid crush
data blocks calibration; no fallback capacity is invented.

For the inspected 2024 records, the resulting limits are:

| Region | Observed annual crush (t) | Modeled capacity (t/month) |
| --- | ---: | ---: |
| Argentina | 43,236,000 | 3,603,000.00 |
| Brazil | 58,162,000 | 4,846,833.33 |
| European Union | 15,400,000 | 1,283,333.33 |
| United States | 66,546,000 | 5,545,500.00 |

Only displayed values are rounded. Processing cost of 35 USD/t and Trader limit
of 5,000 t/month remain assumptions. Margin/utilization bands remain unchanged:
the capacity proxy never forces annual observed throughput.

### Historical conversion yields and the Brazil discrepancy

The inspected Brazilian 2024 observations are internally balanced, including
beginning and ending stocks. All values below are original metric tonnes:

| Attribute | Soybeans | Soybean meal | Soybean oil |
| --- | ---: | ---: | ---: |
| Production | 172,500,000 | 44,383,000 | 11,842,000 |
| Crush input | 58,162,000 | n/a | n/a |
| Domestic consumption | 62,162,000 | 20,500,000 | 10,410,000 |
| Imports | 731,000 | 5,000 | 85,000 |
| Exports | 103,142,000 | 23,390,000 | 1,487,000 |
| Beginning stocks | 29,761,000 | 2,972,000 | 177,000 |
| Ending stocks | 37,688,000 | 3,470,000 | 207,000 |

The 20% oil assumption produces 11,632,400 t, understating source production by
209,600 t. That consumes the 207,000 t reported ending-stock buffer and leaves
the 2,600 t deficit. This annual discrepancy is caused by the conversion ratio,
not floating-point rounding or an inconsistent reported balance. Monthly timing
is a separate assumption.

Historical calibration now supports `calibrate_yields=True`, enabled by the
smoke script. Each product fraction equals its annual production divided by
annual soybean crush. The remaining input fraction is retained explicitly:

```text
meal yield     = 44,383,000 / 58,162,000 = 0.763092740965
oil yield      = 11,842,000 / 58,162,000 = 0.203603727520
residual yield =  1,937,000 / 58,162,000 = 0.033303531515
```

No product ratios are normalized to 100%. Missing, negative, nonfinite, zero-total,
or mass-creating observations block calibration. `CrusherConfig` requires meal,
oil, and explicit residual fractions to account for all input within its existing
numerical tolerance. Synthetic defaults remain 80% meal, 20% oil, zero residual;
capacity-only calibration also retains those defaults unless explicitly enabled.

The residual is unmodeled output: it may reflect by-products, moisture, losses,
or reporting definitions. It is not identified as a specific observed loss.
`SettlementResult.residual_output_tonnes` journals the executed quantity outside
the three traded inventories. It receives no price or revenue. This makes the
modeled mass remainder explicit without inventing another traded commodity.

At full observed annual throughput the derived oil ratio reproduces 11,842,000 t
and leaves exactly the reported 207,000 t ending stocks. The inspected historical
preflight now **passes**, including external trade. Physical execution still
**fails in January**: unchanged Brazilian margin rules choose 60% utilization,
producing 592,100 t oil. Opening stocks plus January imports and this output total
776,183.33 t, below domestic consumption of 867,500 t by 91,316.67 t, before exports.
This is a real utilization/timing constraint, not the former annual yield error.
Neither processing nor consumption is forced to make settlement succeed. The
baseline returns no partial history, and paired execution stops at its failed
baseline. Source revisions may change these diagnostics.

### Physical accounting

Every executed month must satisfy:

```text
ending = opening + physical production + executed imports - consumption - executed exports
```

Soybean production uses the modeled annual allocation; executed crushing consumes
soybeans and produces meal/oil at configured yields. Only product domestic
consumption allocations are applied separately. Annual meal/oil production
references add no inventory. Matched interregional trade subtracts and adds the
same tonnes in ordinary mode; unmatched intentions change nothing. Historical
mode instead records every boundary counterpart once, with an equal and opposite
external ledger movement. Beginning stocks are used once.
For crushing, soybean input equals meal output plus oil output plus recorded
processing residual. Whole-system mass accounting includes that residual as an
explicit outlet; it is not silently lost or credited to product inventory.

Neither artificial stocks, consumption clipping, extra product-production credits,
nor forced processing are used to make execution pass. Offline synthetic tests
still verify complete 12-month runs, physical accounting, and independent drought
comparison with the throughput calibration helper.

`compare_historical_market(prepared, **settings, drought_config=...)` delegates
to the existing paired-run API with identical initial states and fresh agents.
The drought modifies only newly allocated Argentine soybean supply. Both runs
must complete; otherwise the comparison is blocked. The default historical setup
passes preflight but is blocked by baseline physical execution.

Future decisions include installed-capacity/utilization calibration, inventory
alignment, interpreting the residual, non-crush soybean consumption, bilateral trade
coverage, external availability, and responsive trade under drought. Modeled
boundary allocations remain distinct from observed annual trade references.
Price pressure currently follows monthly physical flows rather than direct stock
scarcity. Successful code execution would not establish historical price accuracy.
