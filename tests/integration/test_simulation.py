from datetime import date

from soybean_sim.agents import AgentIdentity, AgentType, Crusher, CrusherConfig
from soybean_sim.data import build_simulation_input
from soybean_sim.market import MarketState
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    MarketBalance,
    MarketSnapshot,
    Region,
)
from soybean_sim.simulation import run_simulation


def test_prepared_inputs_feed_monthly_engine() -> None:
    """Run prepared inputs while keeping annual production out of monthly supply."""
    month = date(2024, 1, 1)
    initial = build_simulation_input(
        balances=tuple(
            MarketBalance(
                region=Region.BRAZIL,
                commodity=commodity,
                marketing_year="2023",
                production_tonnes=1000000,
                imports_tonnes=None,
                exports_tonnes=None,
                domestic_consumption_tonnes=None,
                beginning_stocks_tonnes=stock,
                ending_stocks_tonnes=None,
            )
            for commodity, stock in (
                (Commodity.SOYBEANS, 10000),
                (Commodity.SOYBEAN_MEAL, 0),
                (Commodity.SOYBEAN_OIL, 0),
            )
        ),
        prices=tuple(
            CommodityPrice(
                commodity=commodity,
                month=month,
                price_usd_per_tonne=price,
                data_source="synthetic engine initialization fixture",
            )
            for commodity, price in (
                (Commodity.SOYBEANS, 250),
                (Commodity.SOYBEAN_MEAL, 300),
                (Commodity.SOYBEAN_OIL, 800),
            )
        ),
        marketing_year="2023",
        price_month=month,
        regions=[Region.BRAZIL],
        commodities=list(Commodity),
    )
    state = MarketState.from_snapshot(
        MarketSnapshot(
            month=initial.prices[0].month,
            prices=initial.prices,
            inventories=initial.inventories,
        )
    )
    crusher = Crusher(
        identity=AgentIdentity(
            agent_id="crusher-br", agent_type=AgentType.CRUSHER, region=Region.BRAZIL
        ),
        config=CrusherConfig(
            monthly_capacity_tonnes=4000, processing_cost_usd_per_tonne=10
        ),
    )
    result = run_simulation(
        state,
        [crusher],
        crusher_configs={crusher.identity.agent_id: crusher.config},
        months=3,
    )
    assert result.final_state.snapshot.month == date(2024, 3, 1)
    assert {
        stock.commodity: stock.quantity_tonnes
        for stock in result.final_state.snapshot.inventories
    } == {
        Commodity.SOYBEANS: 0,
        Commodity.SOYBEAN_MEAL: 8000,
        Commodity.SOYBEAN_OIL: 2000,
    }
    assert (
        sum(
            item.executed_tonnes
            for monthly in result.monthly_results
            for item in monthly.settlement_results
        )
        == 10000
    )
    assert all(balance.production_tonnes == 1000000 for balance in initial.balances)
    assert result.initial_state.snapshot.inventories == initial.inventories
