"""Prepare the dashboard's deterministic synthetic market demonstration."""

from dataclasses import dataclass
from datetime import date
from typing import Final

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
    MarketState,
    MonthlyFlowConfig,
    PriceDynamicsConfig,
    generate_monthly_flows,
)
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketBalance,
    MarketSnapshot,
    Region,
)
from soybean_sim.scenarios import (
    ArgentinaDroughtConfig,
    ScenarioComparison,
    run_argentina_drought_comparison,
)

_MONTHS: Final = tuple(date(2025, month, 1) for month in range(1, 13))


@dataclass(frozen=True)
class ComparisonDemo:
    """Keep synthetic common inputs; each run constructs its own agents."""

    state: MarketState
    flows: tuple[MarketFlow, ...]
    crusher_config: CrusherConfig
    price_config: PriceDynamicsConfig
    drought_config: ArgentinaDroughtConfig


def create_demo() -> ComparisonDemo:
    """Build four represented regions with buffers sufficient for twelve months.

    Returns:
        ComparisonDemo: Explicitly synthetic tonne/USD inputs, never rescaled datasets.
    """
    state = MarketState.from_snapshot(
        MarketSnapshot(
            month=_MONTHS[0],
            prices=tuple(
                CommodityPrice(
                    commodity=commodity,
                    month=_MONTHS[0],
                    price_usd_per_tonne=price,
                    data_source="synthetic scenario comparison",
                )
                for commodity, price in (
                    (Commodity.SOYBEANS, 250),
                    (Commodity.SOYBEAN_MEAL, 300),
                    (Commodity.SOYBEAN_OIL, 800),
                )
            ),
            inventories=tuple(
                Inventory(region=region, commodity=commodity, quantity_tonnes=1000)
                for region in Region
                for commodity in Commodity
            ),
        )
    )
    return ComparisonDemo(
        state,
        _create_flows(),
        CrusherConfig(monthly_capacity_tonnes=100, processing_cost_usd_per_tonne=10),
        PriceDynamicsConfig(),
        ArgentinaDroughtConfig(),
    )


def _create_flows() -> tuple[MarketFlow, ...]:
    """Allocate small synthetic annual figures using the existing uniform weights.

    Returns:
        tuple[MarketFlow, ...]: Twelve months of supply and buffered consumption.
    """
    balances = tuple(
        MarketBalance(
            region=region,
            commodity=commodity,
            marketing_year="synthetic",
            production_tonnes=production,
            domestic_consumption_tonnes=consumption,
            imports_tonnes=None,
            exports_tonnes=None,
            beginning_stocks_tonnes=None,
            ending_stocks_tonnes=None,
        )
        for region, commodity, production, consumption in (
            (Region.ARGENTINA, Commodity.SOYBEANS, 2400, None),
            (Region.BRAZIL, Commodity.SOYBEANS, 1200, None),
            (Region.UNITED_STATES, Commodity.SOYBEANS, 1200, None),
            (Region.ARGENTINA, Commodity.SOYBEAN_MEAL, 10800, 480),
            (Region.ARGENTINA, Commodity.SOYBEAN_OIL, 12000, 120),
            (Region.EUROPEAN_UNION, Commodity.SOYBEAN_MEAL, None, 240),
            (Region.EUROPEAN_UNION, Commodity.SOYBEAN_OIL, None, 120),
        )
    )
    return tuple(
        flow
        for month in _MONTHS
        for flow in generate_monthly_flows(
            balances,
            month=month,
            config=MonthlyFlowConfig(),
        )
    )


def create_agents(config: CrusherConfig) -> tuple[Agent, ...]:
    """Create real strategies; Argentina's offers get the matcher's regional priority.

    Args:
        config (CrusherConfig): Immutable Argentina processing settings.

    Returns:
        tuple[Agent, ...]: Three producers, an Argentina crusher, and an EU trader.
    """
    producers = tuple(
        Producer(
            AgentIdentity(
                agent_id=f"producer-{region.value}",
                agent_type=AgentType.PRODUCER,
                region=region,
            ),
            ProducerConfig(reference_price_usd_per_tonne=250),
        )
        for region in (Region.ARGENTINA, Region.BRAZIL, Region.UNITED_STATES)
    )
    crusher = Crusher(
        AgentIdentity(
            agent_id="crusher-ar", agent_type=AgentType.CRUSHER, region=Region.ARGENTINA
        ),
        config,
    )
    trader = Trader(
        AgentIdentity(
            agent_id="trader-eu",
            agent_type=AgentType.TRADER,
            region=Region.EUROPEAN_UNION,
        ),
        TraderConfig(
            reference_soybean_price_usd_per_tonne=250,
            reference_soybean_meal_price_usd_per_tonne=300,
            reference_soybean_oil_price_usd_per_tonne=800,
            maximum_monthly_purchase_tonnes=50,
        ),
    )
    return (*producers, crusher, trader)


def run_demo(case: ComparisonDemo) -> ScenarioComparison:
    """Execute both runs through the public comparison API with fresh concrete agents.

    Args:
        case (ComparisonDemo): Shared synthetic state, flows, and immutable settings.

    Returns:
        ScenarioComparison: Controlled baseline and drought histories for 2025.
    """
    return run_argentina_drought_comparison(
        case.state,
        case.flows,
        drought_config=case.drought_config,
        months=len(_MONTHS),
        agent_factory=lambda: create_agents(case.crusher_config),
        crusher_configs={"crusher-ar": case.crusher_config},
        price_dynamics_config=case.price_config,
    )
