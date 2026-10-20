from datetime import date, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from soybean_sim.models import (
    CommodityPrice,
)


@pytest.mark.parametrize(
    "value", [0, -1, float("nan"), float("inf"), float("-inf"), None, True, "400"]
)
def test_invalid_prices_are_rejected(
    records: dict[type[BaseModel], dict[str, Any]], value: Any
) -> None:
    """Check that prices reject nonpositive, nonfinite, missing, and coerced values."""
    records[CommodityPrice]["price_usd_per_tonne"] = value
    with pytest.raises(ValidationError, match="price_usd_per_tonne"):
        CommodityPrice.model_validate(records[CommodityPrice])


@pytest.mark.parametrize(
    "value",
    [date(2021, 1, 15), datetime(2021, 1, 1), "2021-01", "2021-01-01", 0, None],
)
def test_month_is_an_explicit_first_day_date(
    records: dict[type[BaseModel], dict[str, Any]], value: Any
) -> None:
    """Check that monthly Python inputs require an explicit first-of-month date."""
    records[CommodityPrice]["month"] = value
    with pytest.raises(ValidationError, match="month"):
        CommodityPrice.model_validate(records[CommodityPrice])
