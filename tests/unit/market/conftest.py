from datetime import date

import pytest

from soybean_sim.agents import (
    AgentDecision,
    AgentIdentity,
    AgentType,
    CrusherConfig,
    DecisionAction,
)
from soybean_sim.market import MarketState
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketSnapshot,
    Region,
)


@pytest.fixture
def state() -> MarketState:
    """Provide synthetic Brazilian and US regional stocks for one calendar month."""
    month = date(2021, 1, 1)
    quantities = {
        (Region.BRAZIL, Commodity.SOYBEANS): 10000,
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL): 0,
        (Region.BRAZIL, Commodity.SOYBEAN_OIL): 0,
        (Region.UNITED_STATES, Commodity.SOYBEANS): 100,
        (Region.UNITED_STATES, Commodity.SOYBEAN_MEAL): 5,
        (Region.UNITED_STATES, Commodity.SOYBEAN_OIL): 7,
    }
    return MarketState.from_snapshot(
        MarketSnapshot(
            month=month,
            prices=tuple(
                CommodityPrice(
                    commodity=commodity,
                    month=month,
                    price_usd_per_tonne=400,
                    data_source="synthetic settlement fixture",
                )
                for commodity in Commodity
            ),
            inventories=tuple(
                Inventory(region=region, commodity=commodity, quantity_tonnes=quantity)
                for (region, commodity), quantity in sorted(quantities.items())
            ),
        )
    )


@pytest.fixture
def decision(state: MarketState) -> AgentDecision:
    """Request six thousand soybean input tonnes from a Brazilian crusher."""
    return AgentDecision(
        agent=AgentIdentity(
            agent_id="crusher-b", agent_type=AgentType.CRUSHER, region=Region.BRAZIL
        ),
        month=state.snapshot.month,
        action=DecisionAction.CRUSH,
        commodity=Commodity.SOYBEANS,
        quantity_tonnes=6000,
    )


@pytest.fixture
def configs() -> dict[str, CrusherConfig]:
    """Provide default yields with capacity below the submitted request."""
    return {
        "crusher-b": CrusherConfig(
            monthly_capacity_tonnes=1000, processing_cost_usd_per_tonne=10
        )
    }
