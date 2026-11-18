# Soybean Crush Market Simulator

A Python project for the Advanced Python Programming course. Its agent-based
simulation explores how disruptions affect soybean markets using monthly time
steps. It is a simplified scenario model, not a price forecasting system.

## Goals and scope

The model covers four regions: **United States, Brazil, Argentina, and European
Union**, and three commodities: **soybeans, soybean meal, and soybean oil**.

Three agent types are implemented:

- **Producer** offers available soybeans for sale.
- **Crusher** requests processing of soybeans into soybean meal and soybean oil.
- **Trader** requests soybean purchases and offers soybean products for sale;
  the market matches eligible interregional intentions.

The engine supports monthly physical flows, pricing, trade, and a drought scenario.
Transportation cost scenarios remain planned.

## Current status

Implemented: an installable package, region and commodity enums, immutable
Pydantic configuration and market input models, YAML validation, a Typer CLI,
local USDA PSD CSV and World Bank monthly XLSX price loading, historical record
selection with completeness validation, initial inventories from beginning stocks,
validated simulation input assembly, immutable agent observations and decisions,
an agent interface, a price-responsive Producer and Trader, a margin-based Crusher,
immutable market state, physical crushing settlement, a deterministic monthly
engine with frozen reference prices, tests, and CI.

The engine also supports physical trade, dynamic regional prices, and controlled
baseline/drought comparisons. The dashboard displays simulation results; the CLI
does not start simulation runs.

## Technology stack

Python 3.12+, uv, and Hatchling provide dependency management and packaging.
Pydantic and PyYAML handle configuration; Typer provides the CLI, openpyxl reads
local Excel price data, and Rich supports the existing demonstration reports. Development uses
Ruff, Pyright, pytest, pytest-cov, and GitHub Actions. Streamlit provides the dashboard.

## Installation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/). On macOS
with Homebrew, install both uv and the Just development command runner with:

```bash
brew install uv just
```

Then clone the repository and run these commands from its root:

```bash
git clone <repository-url>
cd soybean-crush-simulator
uv sync --locked
```

Replace `<repository-url>` with this repository's clone URL. uv creates `.venv`
and installs the package in editable mode, including development dependencies by
default. `.python-version` selects Python 3.12; the package requires Python 3.12+.

Use `uv sync --locked --no-dev` for an environment without development tools.
Streamlit is included in the base dependencies. The existing `dashboard` extra
remains available for compatibility.

## Dashboard

Launch from the repository checkout after `uv sync --locked`:

```bash
uv run streamlit run dashboard/app.py
# or: just dashboard
```

Choose synthetic data (fixed 2025) or historical USDA/World Bank inputs. Historical
years are offered only after checking local input coverage and Crusher calibration;
calendar year N uses marketing year N−1 and January N reference prices. Required
files are `data/raw/psd_oilseeds.csv` and
`data/raw/CMO-Historical-Data-Monthly.xlsx`; the app never downloads or substitutes data.

Market scenarios include Baseline, regional soybean Production Shock, meal/oil
Consumption Shock, and regional Crusher Configuration. Choose Single Simulation or
Compare with Baseline. Advanced Settings controls price sensitivity and the monthly
price-change cap, plus inclusive months for flow shocks. Crusher experiments change
capacity and processing cost, with baseline and modified settings previewed before
execution. The synthetic demo has only an Argentina Crusher; historical mode has
one per region. Capacity must stay positive, while processing cost can reach zero.

Only **Run Simulation** executes the model. Controls and chart filters do not rerun
it. Results remain session-local until a new run succeeds; failures retain the last
successful run. The sidebar identifies the completed configuration and pending
changes. Single runs are displayed in Overview and Market Analytics; for paired
runs those views display the baseline, and Scenario Comparison displays both.

Overview shows executed activity and final stocks, with exact values behind compact
KPI tooltips. Analytics supports multiple regions and commodities and a visual-only
price index. Missing observations are gaps; zero stocks are valid observations.
All reported prices are observed during a simulated month, not after its settlement.

Historical opening stocks are marketing-year references, not reconstructed January
inventories. Annual USDA flows are allocated into modeled months; regional prices
are simulated rather than historical replay. Consumption shocks change requested
demand, which may remain unmet. Capacity is a throughput limit, not guaranteed
production, and cost changes may stay within the same utilization band. Trader
behavior does not reconstruct global trade. Valid physical accounting alone is not
economic calibration, and results are not forecasts.

The dashboard requires the repository checkout and editable installation because
its adapter reuses the synthetic demo setup. Rendering components consume completed
results and never execute simulations or load datasets.

## Usage

The CLI provides package information and configuration validation:

```bash
uv run soybean-sim --help
uv run soybean-sim version
uv run soybean-sim validate-config configs/baseline.yaml
uv run python -c "import soybean_sim; print(soybean_sim.__version__)"
```

[The baseline configuration](configs/baseline.yaml) requires `duration_steps`
(a positive integer), `random_seed` (a nonnegative integer), `regions` (a nonempty,
unique selection of supported region identifiers), and `scenario_name` (a trimmed,
nonempty string). Unknown fields and invalid types are rejected. Scenario names
are labels; validation does not establish that a scenario exists.

Validation exits with status 0 on success, or prints a file, YAML, or field error
to stderr and exits with status 1. Incorrect CLI usage exits with status 2.
No command runs a simulation.

Use the [monthly engine Python API](docs/simulation.md) to run prepared market
states with agents and inspect monthly decisions, settlement, and ending stocks.

## Development

Use [Just](https://just.systems/man/en/packages.html) for common development tasks.
Install it with `brew install just` on macOS, or your Linux package manager
(for example, `sudo apt install just` on Ubuntu 24.04+). Recipes call uv, so both
tools must be available on your PATH.

Run `just` to list the available recipes:

```bash
just install        # Install locked dependencies, including all groups
just lint           # Run Ruff lint checks
just format         # Apply Ruff formatting
just format-check   # Check formatting without changing files
just typecheck      # Check Python type annotations with Pyright
just test           # Run pytest with the configured coverage settings
just coverage       # Run pytest with explicit coverage options, matching CI
just check          # Run lint, format-check, typecheck, and coverage
just cli            # Display CLI help
just dashboard      # Launch the Streamlit dashboard
just clean          # Remove caches and test reports; preserve .venv and datasets
```

Recipes run from the directory containing the `justfile`, including when invoked
from a project subdirectory. Use `just check` before opening a pull request.

Tool output paths are configured in `pyproject.toml`. Ruff and pytest caches live
in `.artifacts/ruff/` and `.artifacts/pytest/`; coverage data and optional HTML/XML
reports live in `.artifacts/coverage/`. This directory is generated automatically,
ignored by Git, and removed by `just clean`. Python's `__pycache__/` directories,
the `.venv/` environment, and package builds in `dist/` keep their usual locations
and are also ignored by Git.

Tests include branch coverage reporting with a 90% minimum. CI runs the same
checks as `just check` in independent `lint`, `typecheck`, and `tests` jobs on Python 3.12,
triggered by PRs targeting `main`, pushes to `main`, or manual execution.

Pyright checks `src/` and `dashboard/` in standard mode, targeting Python 3.12. Its development
dependency includes a Node runtime, so no separate Node installation is needed.

For less frequent packaging and dependency tasks, use `uv build` to build the
package, or `uv lock --upgrade` to update dependencies. Review lockfile changes
and rerun `just install` and `just check` after updating dependencies.

## Further documentation

- [Architecture and Python API](docs/architecture.md)
- [Market state and physical settlement](docs/settlement.md)
- [Monthly simulation engine and runnable example](docs/simulation.md)
- [Historical data sources and processing](docs/data-sources.md)
- [Modeling assumptions and open decisions](docs/assumptions.md)
- [Contributing and development workflow](CONTRIBUTING.md)
- [Python style guide](docs/style-guide.md)
