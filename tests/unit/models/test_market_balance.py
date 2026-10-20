from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from soybean_sim.models import (
    Commodity,
    MarketBalance,
    Region,
)


def test_balance_preserves_reporting_period_and_reported_quantities(
    records: dict[type[BaseModel], dict[str, Any]],
) -> None:
    """Check that reported years and quantities need no exact reconciliation."""
    balance = MarketBalance.model_validate(records[MarketBalance])
    assert balance.region is Region.BRAZIL
    assert balance.commodity is Commodity.SOYBEANS
    assert balance.marketing_year == "2020/21"
    assert balance.production_tonnes == 100
    # This fixture intentionally does not satisfy an exact accounting equation.
    assert balance.ending_stocks_tonnes == 12
    assert balance.crush_tonnes is None


@pytest.mark.parametrize(
    "field",
    [
        "production_tonnes",
        "imports_tonnes",
        "exports_tonnes",
        "domestic_consumption_tonnes",
        "beginning_stocks_tonnes",
        "ending_stocks_tonnes",
        "crush_tonnes",
    ],
)
@pytest.mark.parametrize("value", [0, None])
def test_zero_and_missing_observations_remain_distinct(
    records: dict[type[BaseModel], dict[str, Any]], field: str, value: int | None
) -> None:
    """Check that zero and unknown quantities stay distinct through JSON storage."""
    records[MarketBalance][field] = value
    balance = MarketBalance.model_validate(records[MarketBalance])
    assert balance.model_dump()[field] == value
    restored = MarketBalance.model_validate_json(balance.model_dump_json())
    assert restored.model_dump()[field] == value


@pytest.mark.parametrize(
    "field",
    [
        "production_tonnes",
        "imports_tonnes",
        "exports_tonnes",
        "domestic_consumption_tonnes",
        "beginning_stocks_tonnes",
        "ending_stocks_tonnes",
        "crush_tonnes",
    ],
)
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "10"])
def test_balance_rejects_invalid_quantities(
    records: dict[type[BaseModel], dict[str, Any]], field: str, value: Any
) -> None:
    """Check every balance quantity for negative, nonfinite, and coerced values."""
    records[MarketBalance][field] = value
    with pytest.raises(ValidationError, match=field):
        MarketBalance.model_validate(records[MarketBalance])


@pytest.mark.parametrize("commodity", [Commodity.SOYBEAN_MEAL, Commodity.SOYBEAN_OIL])
def test_crush_is_only_a_soybean_input(
    records: dict[type[BaseModel], dict[str, Any]], commodity: Commodity
) -> None:
    """Check that meal and oil reject recorded crush quantities, including zero."""
    records[MarketBalance]["commodity"] = commodity
    assert MarketBalance.model_validate(records[MarketBalance]).crush_tonnes is None
    records[MarketBalance]["crush_tonnes"] = 0
    with pytest.raises(ValidationError, match="applies only to soybeans"):
        MarketBalance.model_validate(records[MarketBalance])


def test_balance_preserves_source_marketing_year_label(
    records: dict[type[BaseModel], dict[str, Any]],
) -> None:
    """Check that source year labels are retained without calendar conversion."""
    records[MarketBalance]["marketing_year"] = "MY 2020/2021"
    balance = MarketBalance.model_validate(records[MarketBalance])
    assert balance.marketing_year == "MY 2020/2021"
