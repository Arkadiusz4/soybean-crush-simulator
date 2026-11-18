from datetime import date

import pytest
from pydantic import ValidationError

from soybean_sim.market import MarketFlow, MarketFlowType
from soybean_sim.models import Commodity, Region
from soybean_sim.scenarios import ProductionShockConfig, apply_production_shock


@pytest.mark.parametrize("region", list(Region))
@pytest.mark.parametrize(
    ("change", "expected"), [(-100, 0), (-30, 70), (0, 100), (20, 120)]
)
def test_region_and_period(region: Region, change: int, expected: float) -> None:
    """Change only soybean supply in the selected region and inclusive month."""
    march, april = date(2025, 3, 1), date(2025, 4, 1)
    flows = tuple(
        MarketFlow(
            month=month,
            region=item,
            commodity=commodity,
            flow_type=kind,
            quantity_tonnes=100,
        )
        for month in (march, april)
        for item in Region
        for commodity, kind in (
            (Commodity.SOYBEANS, MarketFlowType.SUPPLY),
            (Commodity.SOYBEANS, MarketFlowType.CONSUMPTION),
            (Commodity.SOYBEAN_MEAL, MarketFlowType.SUPPLY),
        )
    )
    config = ProductionShockConfig(
        region=region, percentage_change=change, start_month=march, end_month=march
    )
    result = apply_production_shock(flows, config=config)
    for flow in result:
        selected = (
            flow.month == march
            and flow.region == region
            and flow.commodity == Commodity.SOYBEANS
            and flow.flow_type == MarketFlowType.SUPPLY
        )
        assert flow.quantity_tonnes == pytest.approx(expected if selected else 100)
    assert all(flow.quantity_tonnes == 100 for flow in flows)
    assert result == apply_production_shock(reversed(flows), config=config)


@pytest.mark.parametrize("change", [-101, 1001, float("nan"), float("inf")])
def test_invalid_change(change: float) -> None:
    """Reject non-finite and out-of-range percentages."""
    with pytest.raises(ValidationError):
        ProductionShockConfig(region=Region.BRAZIL, percentage_change=change)


def test_invalid_dates() -> None:
    """Reject non-month boundaries and reversed intervals."""
    with pytest.raises(ValidationError):
        ProductionShockConfig(
            region=Region.BRAZIL, percentage_change=0, start_month=date(2025, 1, 2)
        )
    with pytest.raises(ValidationError):
        ProductionShockConfig(
            region=Region.BRAZIL,
            percentage_change=0,
            start_month=date(2025, 2, 1),
            end_month=date(2025, 1, 1),
        )
