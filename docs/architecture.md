# Architecture

## Existing package

The installable distribution is `soybean-crush-simulator`; the Python import
package is `soybean_sim`. Source code lives under `src/soybean_sim/`, and Hatchling
builds the package. The `soybean-sim` entry point invokes the Typer application.

| Component | Responsibility |
| --- | --- |
| `__init__.py` | Read the installed package version |
| `models/` | Shared enums and validated balances, prices, inventories, and simulation inputs |
| `config.py` | Immutable run settings and safe YAML loading |
| `cli.py` | Version display, configuration validation, and terminal errors |
| `agents/` | Producer, crusher, and trader decisions |
| `market/` | Prices, inventories, processing flows, and trade |
| `simulation/` | Monthly run loop, random generator, and scenario effects |
| `data/` inside the package | Local USDA PSD CSV loading; further preprocessing is planned |

Root `configs/` contains a baseline YAML example. Root `data/` is for local datasets
and derived files, ignored by Git except for its README. Unit tests cover enums,
configuration, and file errors; integration tests exercise the CLI.

Configuration loading uses `yaml.safe_load` and Pydantic. `ConfigurationError`
reports file and field context; the CLI converts it to stderr output and a nonzero
exit code. Domain and configuration code do not depend on CLI or dashboard code.

The USDA CSV loader lives in `data/usda/`: `mappings.py` holds verified identifiers
and required columns, `parsing.py` validates observation metadata and quantities,
and `loader.py` reads, filters, and groups CSV rows into `MarketBalance` records.
`errors.py` defines the common data error and file, format, and observation categories.
The package's `__init__.py` preserves the public `load_usda_psd_csv` and
`USDADataError` imports from `soybean_sim.data.usda`.

## Market input models

The following Pydantic models are implemented in `soybean_sim.models` for use by
future ingestion code, the simulation engine, and agents. `models/enums.py` defines
the enums, `models/market.py` contains market records and shared validation types,
and `models/simulation.py` defines `SimulationInput`.

Models reject extra fields and are immutable; collection fields in `SimulationInput` use tuples.

| Model | Fields and purpose |
| --- | --- |
| `MarketBalance` | Region, commodity, original `marketing_year` label, production, imports, exports, domestic consumption, beginning/ending stocks, and optional crush volume |
| `CommodityPrice` | Commodity, `month`, positive `price_usd_per_tonne`, and nonempty `data_source` label |
| `Inventory` | Region, commodity, and known nonnegative `quantity_tonnes` at initialization |
| `SimulationInput` | Nonempty `balances`, `prices`, and `inventories` collections describing an initial state |

All quantity fields end in `_tonnes` and use metric tons, never thousands of tons.

`marketing_year` preserves the source's nonempty string label, without inferring
dates or a calendar-year boundary. JSON uses `YYYY-MM-01`.

## Planned simulation workflow

The engine will combine producer, crusher, and trader agents with regional market
state. Each step will represent one month. The configured step count is already
validated, but no scheduling or calendar behavior exists yet.

A tentative workflow is:

1. Load and validate configuration and prepared historical inputs.
2. Initialize regional state and a random generator from the configured seed.
3. For each month, apply scenario effects, collect agent decisions, reconcile
   feasible production, processing, and trade, then update prices and inventories.
4. Record monthly results for analysis and future visualization.

The ordering within a month and the economic rules remain open. The following
flow beyond configuration validation is planned:

```mermaid
flowchart LR
    Sources[Historical exports] --> Data[Load and validate data]
    Config[Validated configuration] --> Engine[Monthly simulation engine]
    Data --> Engine
    Engine --> Agents[Agent decisions]
    Agents --> Market[Market and inventory updates]
    Market --> Engine
    Engine --> Results[Monthly results]
    Results --> View[Analysis and future dashboard]
```

Data ingestion will provide prepared inputs rather than performing downloads
inside agent decisions. The engine will own run state and compose agents and
market logic, avoiding global mutable state. CLI and future Streamlit code will
call the same Python interfaces; simulation logic will not render UI elements or
read terminal arguments.

## Python API

The existing API supports configuration loading, shared identifiers, and the market
input models described above:

```python
from soybean_sim.config import ConfigurationError, SimulationConfig, load_config
from soybean_sim.models import Commodity, Region

config = load_config("configs/baseline.yaml")
print(config.regions)
```

`load_config(path: str | Path) -> SimulationConfig` returns validated settings
or raises `ConfigurationError`. `Region` and `Commodity` expose the supported
string identifiers. The package also exposes `soybean_sim.__version__`.

The planned simulation API will accept validated configuration and prepared data,
run the monthly model, and return results without depending on the CLI or UI.
Function names, state types, and the results representation are not decided.
There is no callable simulation API yet.

See [assumptions](assumptions.md) for economic decisions and
[data sources](data-sources.md) for input preparation.
