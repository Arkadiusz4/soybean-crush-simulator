# Soybean Crush Market Simulator

A Python project for the Advanced Python Programming course. This agent-based
model explores monthly soybean markets in the United States, Brazil, Argentina,
and the European Union. It covers soybeans, soybean meal, and soybean oil using
synthetic or local historical inputs, configurable experiments, and a Streamlit
dashboard.

## Features

- Monthly Producer, Crusher, and Trader decisions.
- Physical inventories, processing, interregional trade, and regional prices.
- Regional soybean production and meal/oil consumption shocks.
- Crusher capacity and processing cost experiments.
- Baseline comparisons and interactive market analytics.

## Quick start

Install Python 3.12+, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and [Just](https://just.systems/man/en/packages.html). From the repository root:

```bash
uv sync --locked
uv run streamlit run dashboard/app.py
```

Synthetic mode uses the built-in 2025 demonstration and needs no datasets.
Historical mode requires these local files:

- `data/raw/psd_oilseeds.csv`: extract the [USDA PSD Oilseeds ZIP](https://apps.fas.usda.gov/psdonline/downloads/psd_oilseeds_csv.zip).
- `data/raw/CMO-Historical-Data-Monthly.xlsx`: download the monthly Pink Sheet workbook from [World Bank Commodity Markets](https://www.worldbank.org/en/research/commodity-markets).

See [local datasets](data/README.md) for preparation and provenance. The app
checks which historical years have sufficient inputs and valid calibration; it
does not download files automatically.

## Example experiment

1. Choose **Synthetic** data (2025) or historical data and a supported year.
2. Choose **Production Shock**, select Brazil, and set the change to **−30%**.
3. Choose **Compare with Baseline**, then select **Run Simulation**.
4. Explore both runs in **Scenario Comparison**. Overview and Market Analytics
   show the baseline for paired runs; they show the selected result for single runs.

Changing controls or chart filters does not run the simulation. The last successful
result remains available if a new run fails. Advanced settings control price
sensitivity, monthly price limits, and shock dates.

## Project structure

- `src/soybean_sim/`: installable models, data loaders, agents, market, engine, and scenarios.
- `dashboard/`: Streamlit interface and result adapters.
- `tests/`: unit and integration tests with offline fixtures.
- `data/`: instructions and ignored local datasets.
- `docs/`: technical references and modeling assumptions.
- `configs/`: example YAML configuration.

## Development

```bash
just check       # Ruff, formatting, Pyright, and pytest
just integration # Run integration tests (local datasets are optional)
just             # List all available recipes
uv run soybean-sim --help
uv run soybean-sim version
uv run soybean-sim validate-config configs/baseline.yaml
```

The CLI validates configuration and displays version information; it does not run
simulations. See [contributing](CONTRIBUTING.md) and the [style guide](docs/style-guide.md).

### Integration tests and reports

`tests/integration/` contains pytest tests. Files in `tests/support/` provide
the setups, checks, and report formatting those tests call; pytest does not
collect the support files as tests. Run `just integration` to execute the suite.
Checks requiring the two local datasets are skipped when they are unavailable.

To show the terminal reports separately:

```bash
just report-market      # Synthetic market activity
just report-scenarios   # Synthetic baseline versus drought
just report-historical  # Historical comparison; requires local datasets
```

These recipes run the report helpers directly. Some report tests capture output
for assertions, so running pytest with `-s` does not necessarily display it.

## Data and limitations

USDA PSD supplies annual marketing-year balances; World Bank Pink Sheet prices
are monthly global references in USD per metric tonne. Historical calendar year N
uses USDA marketing year N−1 and January N prices. Annual flows are allocated to
modeled months; opening stocks are marketing-year references, not reconstructed
January stocks.

Regional prices and trade follow simplified rules rather than reproducing historical
transactions. Demand can remain unmet. Crusher capacity does not guarantee
throughput. Results are scenarios, not forecasts.

Further details: [data sources](docs/data-sources.md), [historical execution](docs/historical-market.md),
[architecture](docs/architecture.md), [simulation](docs/simulation.md),
[settlement](docs/settlement.md), [scenarios](docs/scenarios.md), and
[assumptions](docs/assumptions.md).
