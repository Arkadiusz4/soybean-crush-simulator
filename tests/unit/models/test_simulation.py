from datetime import date
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketBalance,
    Region,
    SimulationInput,
)


def test_simulation_input_keeps_balances_prices_and_inventory_separate(
    records: dict[type[BaseModel], dict[str, Any]],
) -> None:
    """Check that annual balances, monthly prices, and stocks stay separate."""
    initial = SimulationInput.model_validate(records[SimulationInput])
    assert initial.balances[0].marketing_year == "2020/21"
    assert initial.balances[0].production_tonnes == 100
    assert initial.inventories[0].quantity_tonnes == 5
    assert initial.prices[0].month == date(2021, 1, 1)
    assert initial.prices[0].price_usd_per_tonne == 400
    assert initial.prices[0].data_source == "synthetic test fixture"
    serialized = initial.model_dump(mode="json")
    assert serialized["prices"][0]["month"] == "2021-01-01"
    assert serialized["balances"][0]["region"] == "brazil"


@pytest.mark.parametrize("field", ["balances", "prices", "inventories"])
def test_initial_collections_cannot_be_empty(
    records: dict[type[BaseModel], dict[str, Any]], field: str
) -> None:
    """Check that a snapshot requires at least one record in each input collection."""
    records[SimulationInput][field] = []
    with pytest.raises(ValidationError, match=field):
        SimulationInput.model_validate(records[SimulationInput])


@pytest.mark.parametrize("field", ["balances", "prices", "inventories"])
def test_initial_records_must_be_unique(
    records: dict[type[BaseModel], dict[str, Any]], field: str
) -> None:
    """Check that repeated records in each snapshot collection fail validation."""
    records[SimulationInput][field] *= 2
    with pytest.raises(ValidationError, match=f"{field} must be unique"):
        SimulationInput.model_validate(records[SimulationInput])


def test_initial_prices_must_share_one_month(
    records: dict[type[BaseModel], dict[str, Any]],
) -> None:
    """Check that different commodities cannot have different initial price months."""
    second_price = {
        **records[CommodityPrice],
        "commodity": "soybean_oil",
        "month": date(2021, 2, 1),
    }
    records[SimulationInput]["prices"].append(second_price)
    with pytest.raises(ValidationError, match="same month"):
        SimulationInput.model_validate(records[SimulationInput])


def test_initial_input_accepts_multiple_regions_and_commodities(
    records: dict[type[BaseModel], dict[str, Any]],
) -> None:
    """Check snapshots containing several regions and commodities."""
    second_balance = {
        **records[MarketBalance],
        "region": "united_states",
        "commodity": "soybean_oil",
        "marketing_year": "2021",
    }
    second_inventory = {
        **records[Inventory],
        "region": "united_states",
        "commodity": "soybean_oil",
    }
    second_price = {**records[CommodityPrice], "commodity": "soybean_oil"}
    records[SimulationInput]["balances"].append(second_balance)
    records[SimulationInput]["inventories"].append(second_inventory)
    records[SimulationInput]["prices"].append(second_price)
    initial = SimulationInput.model_validate(records[SimulationInput])
    assert initial.balances[1].region is Region.UNITED_STATES
    assert initial.balances[1].marketing_year == "2021"
    assert initial.inventories[1].commodity is Commodity.SOYBEAN_OIL
    assert initial.prices[1].month == initial.prices[0].month
