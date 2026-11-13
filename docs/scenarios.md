# Scenario input preparation

`soybean_sim.scenarios` prepares monthly flows before execution. It does not run
simulations or select scenarios from `SimulationConfig.scenario_name`; that field
remains a label. No scenario registry or YAML dispatcher is implemented.

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

## Modeling assumptions and remaining work

The drought is an exogenous reduction in new soybean production. The default is
a synthetic modeling assumption, not an estimate of a particular historical
drought. It does not destroy existing stocks or change consumption, crushing
capacity, prices, trade intentions, or executions directly.

Downstream price and trade effects depend on the existing market model when
adjusted flows are supplied to `run_simulation(monthly_flows=...)`. A 30% production
reduction does not imply a 30% price increase. Scenario execution workflows and
baseline-versus-drought comparisons/reporting remain for the next PR.
