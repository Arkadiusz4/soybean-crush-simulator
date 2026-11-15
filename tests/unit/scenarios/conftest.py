from datetime import date
from typing import Any

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
from soybean_sim.market import (
    MarketFlow,
    MarketFlowType,
    MarketState,
    PriceDynamicsConfig,
    initialize_regional_prices,
)
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketSnapshot,
    Region,
)
from soybean_sim.scenarios import ArgentinaDroughtConfig


@pytest.fixture
def comparison_inputs() -> dict[str, Any]:
    """Provide a stock-limited market where reduced supply also reduces executed trade.

    Returns:
        dict[str, Any]: Paired-run arguments with real strategies and fresh agents.
    """
    month = date(2025, 1, 1)
    state = MarketState.from_snapshot(
        MarketSnapshot(
            month=month,
            prices=tuple(
                CommodityPrice(
                    commodity=commodity,
                    month=month,
                    price_usd_per_tonne=price,
                    data_source="synthetic comparison fixture",
                )
                for commodity, price in (
                    (Commodity.SOYBEANS, 250),
                    (Commodity.SOYBEAN_MEAL, 300),
                    (Commodity.SOYBEAN_OIL, 800),
                )
            ),
            inventories=tuple(
                Inventory(region=region, commodity=commodity, quantity_tonnes=0)
                for region in (Region.ARGENTINA, Region.BRAZIL)
                for commodity in Commodity
            ),
        )
    )
    config = CrusherConfig(monthly_capacity_tonnes=80, processing_cost_usd_per_tonne=10)

    def create_agents() -> tuple[Agent, ...]:
        """Create three independent concrete agents with identical strategy settings.

        Returns:
            tuple[Agent, ...]: Argentina producer/crusher and Brazilian buyer.
        """
        return (
            Producer(
                AgentIdentity(
                    agent_id="producer-ar",
                    agent_type=AgentType.PRODUCER,
                    region=Region.ARGENTINA,
                ),
                ProducerConfig(
                    reference_price_usd_per_tonne=250,
                    low_supply_fraction=1,
                    normal_supply_fraction=1,
                    high_supply_fraction=1,
                ),
            ),
            Crusher(
                AgentIdentity(
                    agent_id="crusher-ar",
                    agent_type=AgentType.CRUSHER,
                    region=Region.ARGENTINA,
                ),
                config,
            ),
            Trader(
                AgentIdentity(
                    agent_id="trader-br",
                    agent_type=AgentType.TRADER,
                    region=Region.BRAZIL,
                ),
                TraderConfig(
                    reference_soybean_price_usd_per_tonne=250,
                    reference_soybean_meal_price_usd_per_tonne=300,
                    reference_soybean_oil_price_usd_per_tonne=800,
                    maximum_monthly_purchase_tonnes=100,
                ),
            ),
        )

    return dict(
        initial_state=state,
        baseline_flows=tuple(
            MarketFlow(
                month=date(2025, number, 1),
                region=Region.ARGENTINA,
                commodity=Commodity.SOYBEANS,
                flow_type=MarketFlowType.SUPPLY,
                quantity_tonnes=100,
            )
            for number in (1, 2)
        ),
        drought_config=ArgentinaDroughtConfig(),
        months=2,
        agent_factory=create_agents,
        crusher_configs={"crusher-ar": config},
        price_dynamics_config=PriceDynamicsConfig(),
        regional_prices=initialize_regional_prices(
            state.snapshot, [Region.ARGENTINA, Region.BRAZIL]
        ),
    )
