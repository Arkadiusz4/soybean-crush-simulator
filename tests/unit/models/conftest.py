from datetime import date
from typing import Any

import pytest
from pydantic import BaseModel

from soybean_sim.models import (
    CommodityPrice,
    Inventory,
    MarketBalance,
    SimulationInput,
)


@pytest.fixture
def records() -> dict[type[BaseModel], dict[str, Any]]:
    """Return fresh synthetic inputs for each market model and the initial snapshot.

    Quantities are illustrative, not historical data. The annual balance is
    intentionally not exact so tests do not imply an accounting constraint.
    """
    balance = {
        "region": "brazil",
        "commodity": "soybeans",
        "marketing_year": "2020/21",
        "production_tonnes": 100,
        "imports_tonnes": 10,
        "exports_tonnes": 20,
        "domestic_consumption_tonnes": 80,
        "beginning_stocks_tonnes": 5,
        "ending_stocks_tonnes": 12,
    }
    price = {
        "commodity": "soybeans",
        "month": date(2021, 1, 1),
        "price_usd_per_tonne": 400,
        "data_source": "synthetic test fixture",
    }
    inventory = {"region": "brazil", "commodity": "soybeans", "quantity_tonnes": 5}
    return {
        MarketBalance: balance,
        CommodityPrice: price,
        Inventory: inventory,
        SimulationInput: {
            "balances": [balance],
            "prices": [price],
            "inventories": [inventory],
        },
    }
