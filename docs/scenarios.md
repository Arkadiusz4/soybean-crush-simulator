# Scenario input preparation

`soybean_sim.scenarios` prepares drought flows and executes controlled paired runs.
`SimulationConfig.scenario_name` remains a label; there is no scenario registry or
YAML dispatcher.

## Argentina drought

```python
from datetime import date

from soybean_sim.scenarios import ArgentinaDroughtConfig, apply_argentina_drought

# baseline_flows contains MarketFlow records from generate_monthly_flows().
drought_flows = apply_argentina_drought(
    baseline_flows,
    config=ArgentinaDroughtConfig(
        production_reduction_fraction=0.30,
        start_month=date(2025, 1, 1),
        end_month=date(2025, 3, 1),
    ),
)
```

Only Argentina soybean SUPPLY within the inclusive interval is multiplied by
`1 - production_reduction_fraction`: 1,000 tonnes becomes 700 at the default 30%.
Zero means no reduction; one means complete loss of new supply, retaining an
explicit zero record. Bounds must be first-of-month dates. Omit either bound for
an unbounded side, or both to cover every supplied month.

The frozen configuration rejects unknown fields, non-finite or out-of-range
fractions, booleans, numeric strings, invalid dates, and reversed intervals.
Construction errors use Pydantic's `ValidationError`. The transformation
revalidates the full input and settings, raising `ScenarioError` for invalid
objects or records, duplicate month/region/commodity/type keys, and unsupported
soybean consumption. Adjusted flows are validated as finite nonnegative tonnes.
Empty and partial collections are allowed; missing flows are never invented.

The result is an immutable tuple sorted by month, region, commodity, and flow
type. Baseline flows remain unchanged and independently usable. Quantities for
other regions, product supply references, and product consumption are preserved.
Preparation follows the existing annual-to-monthly allocation; it introduces no
new allocation or alignment between USDA marketing years and calendar months.

## Paired execution and comparison

Given a prepared initial state, flows, and a factory that creates fresh agents:

```python
from soybean_sim.market import PriceDynamicsConfig
from soybean_sim.scenarios import run_argentina_drought_comparison

comparison = run_argentina_drought_comparison(
    initial_state,
    baseline_flows,
    drought_config=ArgentinaDroughtConfig(),
    months=12,
    agent_factory=create_agents,
    crusher_configs=crusher_configs,
    price_dynamics_config=PriceDynamicsConfig(),
    # Optional: regional_prices=initial_regional_prices,
)
monthly = comparison.monthly_metrics()
```

The runner materializes generator inputs once, applies the existing drought
transformation, and runs the unchanged engine twice. Both runs share initial
stocks, global/regional price baselines, processing settings, duration, and price
dynamics. The factory is called separately for each run. Reusing agent objects,
changing identities/implementation classes, or changing concrete Producer,
Crusher, or Trader configurations raises `ScenarioError`. Custom `Agent`
implementations remain supported; their factory must also reproduce private
strategy settings and create independent mutable state.

Frozen `ScenarioComparison` holds `baseline`, `drought`, and `drought_config` once.
It checks matching months and initial conditions, physical-cycle history, complete
inventory/price/pressure coverage for all three commodities in represented regions,
finite nested values, and the configured supply-only flow difference. Existing
engine/settlement domain errors propagate. `compare_simulation_results()` validates
already recorded histories, but cannot infer unrecorded private strategy settings.
Invalid comparison records raise `ScenarioError`; direct model construction uses
Pydantic validation errors.

`monthly_metrics()` returns immutable `MonthlyComparison` values:

- `markets`: sorted `RegionalMarketComparison` values containing same-month
  `price_usd_per_tonne`, ending `inventory_tonnes`, executed `imports_tonnes`,
  `exports_tonnes`, `net_trade_tonnes`, and recorded `supply_tonnes`/`demand_tonnes`.
  Net trade means imports minus exports, matching inventory flow signs.
- `crushing_tonnes`: sorted `(Region, MetricComparison)` pairs using executed
  soybean inputs, including actual zero activity.
- `trade_volume_tonnes`, `baseline_trade_count`, and `drought_trade_count`:
  actual transfers counted once, excluding unmatched intentions.

Each `MetricComparison` exposes `baseline`, `drought`, `difference` (drought minus
baseline), and `percentage_difference`. Percentage change is undefined (`None`)
when the baseline is zero; absolute zero differences remain explicit. Regional
prices always have positive baselines. Missing observations are rejected rather
than filled with zeros. Pressures come directly from recorded history; no pressure
or pricing algorithm is rerun to derive comparison metrics.

Prices are those agents observed during the same month. First-month prices are
identical even though drought flows affect that month's activity; effects can
reach observed prices the following month. December's observations are not a
post-December price forecast.

## Synthetic demonstration

```bash
uv run python scripts/compare_scenarios.py
uv run python scripts/compare_scenarios.py --verbose
```

The dataset-free demo covers January–December 2025 and all four regions. It starts
every regional commodity with 1,000 tonnes, uses three real Producers, an Argentina
Crusher (100 t/month, 10 USD/t processing cost, default yields), and an EU Trader
(50 t/month soybean purchase limit). Toy annual supply is 2,400 tonnes for Argentina
and 1,200 each for Brazil and the US, allocated uniformly. Argentina meal/oil
production remains reference-only; small product consumption is covered by initial
buffers and crushing. These are explicit synthetic quantities, not normalized USDA
observations. No datasets are loaded or rescaled.

The report compares the last observed month's regional prices, executed period
totals, and selected ending stocks. Verbose mode adds monthly price, crushing,
trade-volume, and inventory trajectories. Validation checks physical stock
accounting independently, common inputs/coverage, repeatability, and baseline
isolation. Failures exit nonzero with a named stage and no overall PASS.

For these settings, soybean supply in Argentina falls from 2,400 to 1,680 tonnes.
Executed crushing is 960 versus 850 tonnes; trade remains 575 tonnes in both runs
because existing offers still fulfill the EU's intentions. Argentina's prices
change while other regions' observed price paths remain unchanged. These values
describe this controlled experiment, not a general economic prediction.

## Economic interpretation and limitations

The drought is an exogenous reduction in new soybean production. The default is
a synthetic modeling assumption, not an estimate of a particular historical
drought. It does not destroy existing stocks or change consumption, crushing
capacity, prices, trade intentions, or executions directly.

Downstream price and trade effects depend on the existing market model when
adjusted flows are supplied to `run_simulation(monthly_flows=...)`. A 30% production
reduction does not imply a 30% price increase. Prices respond to monthly physical
flows, not directly to stock scarcity. Crushing and trading follow only the existing
strategies and matching priority; unchanged results are valid model outcomes.

There is no guarantee of transmission from Argentina to every other region.
Different price paths require an actual physical or behavioral channel in the
simulated market. Global historical prices remain fixed reference values;
regional prices are simulated, uncalibrated values. The comparison is a controlled
scenario experiment, not a market-price forecast. Empirical calibration, weather
estimation, new strategies, and dashboard integration remain outside this feature.
