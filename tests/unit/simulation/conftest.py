from datetime import date

import pytest

from soybean_sim.agents import (
    Agent,
    AgentIdentity,
    AgentType,
    Crusher,
    CrusherConfig,
    Producer,
    ProducerConfig,
    Trader,
    TraderConfig,
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
    """Provide synthetic starting stocks and profitable November reference prices."""
    month = date(2023, 11, 1)
    return MarketState.from_snapshot(
        MarketSnapshot(
            month=month,
            prices=tuple(
                CommodityPrice(
                    commodity=commodity,
                    month=month,
                    price_usd_per_tonne=price,
                    data_source="synthetic monthly engine fixture",
                )
                for commodity, price in (
                    (Commodity.SOYBEANS, 250),
                    (Commodity.SOYBEAN_MEAL, 300),
                    (Commodity.SOYBEAN_OIL, 800),
                )
            ),
            inventories=tuple(
                Inventory(
                    region=Region.BRAZIL, commodity=commodity, quantity_tonnes=quantity
                )
                for commodity, quantity in (
                    (Commodity.SOYBEAN_MEAL, 0),
                    (Commodity.SOYBEAN_OIL, 0),
                    (Commodity.SOYBEANS, 10000),
                )
            ),
        )
    )


@pytest.fixture
def configs() -> dict[str, CrusherConfig]:
    """Provide a four-thousand-tonne monthly capacity with default processing yields."""
    return {
        "crusher-br": CrusherConfig(
            monthly_capacity_tonnes=4000,
            processing_cost_usd_per_tonne=10,
        )
    }


@pytest.fixture
def agents(configs: dict[str, CrusherConfig]) -> tuple[Agent, ...]:
    """Provide all concrete agent roles in a deliberately noncanonical input order."""
    return (
        Trader(
            identity=AgentIdentity(
                agent_id="trader-br", agent_type=AgentType.TRADER, region=Region.BRAZIL
            ),
            config=TraderConfig(
                reference_soybean_price_usd_per_tonne=250,
                reference_soybean_meal_price_usd_per_tonne=300,
                reference_soybean_oil_price_usd_per_tonne=800,
                maximum_monthly_purchase_tonnes=1000,
            ),
        ),
        Producer(
            identity=AgentIdentity(
                agent_id="producer-br",
                agent_type=AgentType.PRODUCER,
                region=Region.BRAZIL,
            ),
            config=ProducerConfig(reference_price_usd_per_tonne=250),
        ),
        Crusher(
            identity=AgentIdentity(
                agent_id="crusher-br",
                agent_type=AgentType.CRUSHER,
                region=Region.BRAZIL,
            ),
            config=configs["crusher-br"],
        ),
    )
