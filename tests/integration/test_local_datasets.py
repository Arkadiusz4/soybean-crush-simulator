"""Optional end-to-end checks against locally downloaded historical data."""

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

from soybean_sim.data import build_simulation_input
from soybean_sim.data.usda import load_usda_psd_csv
from soybean_sim.data.world_bank import load_world_bank_prices
from soybean_sim.models import Commodity, Region

_ROOT = Path(__file__).resolve().parents[2]
_SUPPORT = _ROOT / "tests/support"
_DATASETS = (
    _ROOT / "data/raw/psd_oilseeds.csv",
    _ROOT / "data/raw/CMO-Historical-Data-Monthly.xlsx",
)
pytestmark = pytest.mark.skipif(
    not all(path.is_file() for path in _DATASETS),
    reason="Optional local USDA and World Bank datasets are unavailable",
)


def test_local_data_preparation() -> None:
    """Load the real sources and verify the assembled initial market coverage."""
    month = date(2025, 1, 1)
    balances = load_usda_psd_csv(_DATASETS[0], marketing_year="2024")
    prices = load_world_bank_prices(_DATASETS[1], start_month=month, end_month=month)
    initial = build_simulation_input(
        balances,
        prices,
        marketing_year="2024",
        price_month=month,
        regions=list(Region),
        commodities=list(Commodity),
    )
    assert len(initial.balances) == 12
    assert len(initial.prices) == 3
    assert len(initial.inventories) == 12


def test_local_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 12-month simulation invariants with the optional real inputs."""
    specification = importlib.util.spec_from_file_location(
        "local_simulation_smoke", _SUPPORT / "smoke_test_simulation.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    monkeypatch.setitem(sys.modules, specification.name, module)
    specification.loader.exec_module(module)
    assert module.main() == 0
