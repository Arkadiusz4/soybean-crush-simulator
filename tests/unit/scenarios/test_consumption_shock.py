from datetime import date

import pytest
from pydantic import ValidationError

from soybean_sim.market import MarketFlow, MarketFlowType
from soybean_sim.models import Commodity, Region
from soybean_sim.scenarios import ConsumptionShockConfig, apply_consumption_shock


@pytest.mark.parametrize("region", list(Region))
@pytest.mark.parametrize("commodity", [Commodity.SOYBEAN_MEAL, Commodity.SOYBEAN_OIL])
@pytest.mark.parametrize("change", [-100, -20, 0, 30])
def test_consumption_transformation(
    region: Region, commodity: Commodity, change: int
) -> None:
    """Change only selected consumption and preserve input records and ordering."""
    first, second = date(2025, 1, 1), date(2025, 2, 1)
    flows = tuple(
        MarketFlow(
            month=month,
            region=item,
            commodity=product,
            flow_type=kind,
            quantity_tonnes=100,
        )
        for month in (first, second)
        for item in Region
        for product in (
            Commodity.SOYBEANS,
            Commodity.SOYBEAN_MEAL,
            Commodity.SOYBEAN_OIL,
        )
        for kind in (MarketFlowType.SUPPLY, MarketFlowType.CONSUMPTION)
    )
    config = ConsumptionShockConfig(
        region=region,
        commodity=commodity,
        percentage_change=change,
        start_month=first,
        end_month=first,
    )
    result = apply_consumption_shock(flows, config=config)
    assert result == apply_consumption_shock(reversed(flows), config=config)
    assert all(item.quantity_tonnes == 100 for item in flows)
    for item in result:
        target = (
            item.month == first
            and item.region == region
            and item.commodity == commodity
            and item.flow_type == MarketFlowType.CONSUMPTION
        )
        assert item.quantity_tonnes == pytest.approx(100 + change if target else 100)


@pytest.mark.parametrize("change", [-101, 1001, float("nan"), float("inf")])
def test_invalid_shock(change: float) -> None:
    """Reject out-of-range and nonfinite changes."""
    with pytest.raises(ValidationError):
        ConsumptionShockConfig(
            region=Region.BRAZIL,
            commodity=Commodity.SOYBEAN_OIL,
            percentage_change=change,
        )


def test_unsupported_and_missing() -> None:
    """Reject soybeans and avoid creating absent consumption observations."""
    with pytest.raises(ValidationError):
        ConsumptionShockConfig(
            region=Region.BRAZIL, commodity=Commodity.SOYBEANS, percentage_change=30
        )
    config = ConsumptionShockConfig(
        region=Region.BRAZIL, commodity=Commodity.SOYBEAN_OIL, percentage_change=30
    )
    assert apply_consumption_shock((), config=config) == ()
