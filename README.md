# Soybean Crush Market Simulator

A Python project for the Advanced Python Programming course. The planned
agent-based simulation will explore how disruptions affect soybean markets using
monthly time steps. It is a simplified scenario model, not a price forecasting
system.

## Goals and scope

The model will cover four regions: **United States, Brazil, Argentina, and European
Union**, and three commodities: **soybeans, soybean meal, and soybean oil**.

Three agent types are planned:

- **Producer** produces soybeans and supplies them to the market.
- **Crusher** buys soybeans and processes them into soybean meal and soybean oil.
- **Trader** moves commodities between regions based on prices, availability,
  and transportation costs.

Planned features include monthly production, processing, inventory, price, and
trade updates; drought, demand, and transportation cost scenarios; historical
initialization from USDA PSD and World Bank data; and an optional Streamlit dashboard.

## Current status

Implemented: an installable package, region and commodity enums, immutable
Pydantic configuration models, YAML validation, a Typer CLI, tests, and CI.

Agents, the simulation engine, economic rules, historical data ingestion, scenario
effects, results, and dashboard functionality are **not implemented**. Monthly
steps are a modeling decision for the future engine; the current configuration
only validates a positive step count.

## Technology stack

Python 3.12+, uv, and Hatchling provide dependency management and packaging.
Pydantic and PyYAML handle configuration; Typer provides the CLI. Development uses
Ruff, pytest, pytest-cov, and GitHub Actions. Streamlit is an optional dependency.

## Installation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then clone
the repository and run these commands from its root:

```bash
git clone <repository-url>
cd soybean-crush-simulator
uv sync --locked
```

Replace `<repository-url>` with this repository's clone URL. uv creates `.venv`
and installs the package in editable mode, including development dependencies by
default. `.python-version` selects Python 3.12; the package requires Python 3.12+.

Use `uv sync --locked --no-dev` for an environment without development tools.
To install optional dashboard dependencies:

```bash
uv sync --locked --extra dashboard
```

This extra does not provide a dashboard application yet.

## Usage

Only package information and configuration validation are available:

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

## Development

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

Use `uv run ruff format .` to apply formatting. Tests include branch coverage
reporting with a 90% minimum. CI has independent `lint` and `tests` jobs on Python
3.12, triggered by PRs targeting `main`, pushes to `main`, or manual execution.
Both jobs use `uv sync --locked --all-groups`.

When intentionally updating dependencies, run `uv lock --upgrade`, review
`uv.lock`, and rerun the checks.

## Further documentation

- [Architecture and Python API](docs/architecture.md)
- [Historical data sources and processing](docs/data-sources.md)
- [Modeling assumptions and open decisions](docs/assumptions.md)
- [Contributing and development workflow](CONTRIBUTING.md)
