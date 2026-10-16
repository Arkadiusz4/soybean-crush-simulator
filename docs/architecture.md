# Architecture

## Existing package

The installable distribution is `soybean-crush-simulator`; the Python import
package is `soybean_sim`. Source code lives under `src/soybean_sim/`, and Hatchling
builds the package. The `soybean-sim` entry point invokes the Typer application.

| Component | Responsibility |
| --- | --- |
| `__init__.py` | Read the installed package version |
| `models.py` | Shared `Region` and `Commodity` enums |
| `config.py` | Immutable run settings and safe YAML loading |
| `cli.py` | Version display, configuration validation, and terminal errors |
| `agents/` | Producer, crusher, and trader decisions |
| `market/` | Prices, inventories, processing flows, and trade |
| `simulation/` | Monthly run loop, random generator, and scenario effects |
| `data/` inside the package | Historical data loading and preprocessing |

Root `configs/` contains a baseline YAML example. Root `data/` is for local datasets
and derived files, ignored by Git except for its README. Unit tests cover enums,
configuration, and file errors; integration tests exercise the CLI.

Configuration loading uses `yaml.safe_load` and Pydantic. `ConfigurationError`
reports file and field context; the CLI converts it to stderr output and a nonzero
exit code. Domain and configuration code do not depend on CLI or dashboard code.

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

The existing API supports configuration loading and shared identifiers:

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
