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
Pydantic configuration and market input models, YAML validation, a Typer CLI,
local USDA PSD CSV loading, tests, and CI.

Agents, the simulation engine, economic rules, World Bank ingestion, scenario
effects, results, and dashboard functionality are **not implemented**. Monthly
steps are a modeling decision for the future engine; the current configuration
only validates a positive step count.

## Technology stack

Python 3.12+, uv, and Hatchling provide dependency management and packaging.
Pydantic and PyYAML handle configuration; Typer provides the CLI. Development uses
Ruff, Pyright, pytest, pytest-cov, and GitHub Actions. Streamlit is an optional dependency.

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
just clean          # Remove caches and test reports; preserve .venv and datasets
```

Recipes run from the directory containing the `justfile`, including when invoked
from a project subdirectory. Use `just check` before opening a pull request.

Tests include branch coverage reporting with a 90% minimum. CI runs the same
checks as `just check` in independent `lint`, `typecheck`, and `tests` jobs on Python 3.12,
triggered by PRs targeting `main`, pushes to `main`, or manual execution.

Pyright checks `src/` in standard mode, targeting Python 3.12. Its development
dependency includes a Node runtime, so no separate Node installation is needed.

For less frequent packaging and dependency tasks, use `uv build` to build the
package, or `uv lock --upgrade` to update dependencies. Review lockfile changes
and rerun `just install` and `just check` after updating dependencies.

## Further documentation

- [Architecture and Python API](docs/architecture.md)
- [Historical data sources and processing](docs/data-sources.md)
- [Modeling assumptions and open decisions](docs/assumptions.md)
- [Contributing and development workflow](CONTRIBUTING.md)
