# Soybean Crush Market Simulator

A university project for the Advanced Python Programming course. The planned
agent-based model will explore the soybean, soybean meal, and soybean oil markets
across the United States, Brazil, Argentina, and European Union.

**Current status:** this repository provides the package foundation, configuration
validation, CLI, tests, and development tooling. It does not run simulations or
produce market results.

## Planned features

- Producer, crusher, and trader agents responding to prices, inventories, supply,
  demand, and processing profitability.
- Regional market interactions and trade across the three commodities.
- Scenarios for droughts, demand changes, and transportation cost changes.
- Historical data ingestion from USDA PSD and World Bank Commodity Markets.
- An optional Streamlit dashboard.

Economic decision rules, market clearing, calibration, scenario effects, data
integrations, and dashboard behavior are all future work.

## Technology stack

Python 3.12+, Hatchling for packaging, uv for dependency management, Pydantic for
configuration models, PyYAML for safe YAML loading, and Typer for the CLI. Ruff,
pytest, pytest-cov, and GitHub Actions support development. Streamlit is
declared only as an optional dependency; no dashboard exists yet.

## Installation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), clone this
repository, and open its root directory:

```bash
git clone <repository-url>
cd soybean-crush-simulator
uv sync --locked
```

`uv sync` creates `.venv`, installs the package in editable mode, and includes the
development dependency group by default. `.python-version` selects Python 3.12
for local development. Python 3.12 and later are supported; CI checks Python 3.12.
See uv's [project dependency documentation](https://docs.astral.sh/uv/concepts/projects/dependencies/).

For an application environment without development tools, use `uv sync --locked
--no-dev`. To also install the future dashboard's dependencies, use:

```bash
uv sync --locked --extra dashboard
```

The lockfile includes the optional dependency resolution. Installing this extra
does not provide a dashboard command or page.

## Usage

```bash
uv run soybean-sim --help
uv run soybean-sim version
uv run soybean-sim validate-config configs/baseline.yaml
uv run python -c "import soybean_sim; print(soybean_sim.__version__)"
```

Validation prints a confirmation and exits with status 0 on success. File, YAML,
or schema failures print an error to stderr and exit with status 1; incorrect CLI
usage exits with status 2. No command executes a simulation.

The configuration format is a flat UTF-8 YAML mapping:

```yaml
duration_steps: 120
random_seed: 42
regions:
  - united_states
  - brazil
  - argentina
  - european_union
scenario_name: baseline
```

All fields are required. `duration_steps` is a positive integer and `random_seed`
is a nonnegative integer; booleans, floats, and quoted numbers are rejected.
`regions` must be a nonempty list of unique supported identifiers. `scenario_name`
is a nonempty string, with surrounding whitespace removed. Unknown fields are
rejected. Scenario names are labels only: validation does not check for an
implemented scenario. A step has no assigned calendar duration yet.

The same validation is available without the CLI:

```python
from soybean_sim.config import load_config

config = load_config("configs/baseline.yaml")
print(config.regions)
```

## Development

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

Use `uv run ruff format .` to apply formatting. Tests report branch coverage and
require at least 90% coverage of the implemented package. CI runs on pull requests
targeting `main`, pushes to `main`, and manual requests. Independent `lint` and
`tests` jobs install dependencies with `uv sync --locked --all-groups` and check
Ruff and tests with coverage. Outdated runs for the same PR are canceled. Its
setup follows the [uv GitHub Actions guide](https://docs.astral.sh/uv/guides/integration/github/).

When intentionally updating dependencies, run `uv lock --upgrade`, review
`uv.lock`, and rerun the checks.

See [contributing](CONTRIBUTING.md), [architecture](docs/architecture.md),
[data sources](docs/data-sources.md), and [assumptions](docs/assumptions.md).
