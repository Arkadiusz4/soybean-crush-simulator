from dataclasses import dataclass, field
from datetime import date

import pytest

from soybean_sim.agents import (
    AgentDecision,
    AgentIdentity,
    AgentType,
    Crusher,
    CrusherConfig,
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
from soybean_sim.simulation import SimulationResult, run_simulation


@dataclass
class _PriceObserver:
    """Capture exact regional views without adding synthetic market activity."""

    identity: AgentIdentity
    observations: list[MarketSnapshot] = field(default_factory=list)

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Record the view supplied by the engine and abstain.

        Args:
            snapshot (MarketSnapshot): Current regional prices and shared stocks.

        Returns:
            tuple[AgentDecision, ...]: Empty intentions.
        """
        self.observations.append(snapshot)
        return ()


def test_dynamic_prices_follow_actual_activity_across_year_boundary() -> None:
    """Connect physical flows, configured crushing, pressure, and next-month views.

    Returns:
        None: Confirms exact histories, immutable inputs, and deterministic feedback.
    """
    december, january = date(2023, 12, 1), date(2024, 1, 1)
    global_prices = {
        Commodity.SOYBEANS: 250,
        Commodity.SOYBEAN_MEAL: 300,
        Commodity.SOYBEAN_OIL: 800,
    }
    regions = (Region.BRAZIL, Region.EUROPEAN_UNION)
    initial = MarketState.from_snapshot(
        MarketSnapshot(
            month=december,
            prices=tuple(
                CommodityPrice(
                    commodity=commodity,
                    month=december,
                    price_usd_per_tonne=value,
                    data_source="synthetic dynamic integration",
                )
                for commodity, value in global_prices.items()
            ),
            inventories=tuple(
                Inventory(
                    region=region,
                    commodity=commodity,
                    quantity_tonnes=1000 if commodity == Commodity.SOYBEANS else 100,
                )
                for region in regions
                for commodity in sorted(Commodity)
            ),
        )
    )
    baselines = tuple(
        price.model_copy(update={"price_usd_per_tonne": price.price_usd_per_tonne * 2})
        if price.region == Region.EUROPEAN_UNION
        else price
        for price in initialize_regional_prices(initial.snapshot, regions)
    )
    config = CrusherConfig(
        monthly_capacity_tonnes=100, processing_cost_usd_per_tonne=10
    )
    crusher = Crusher(
        AgentIdentity(
            agent_id="crusher-br", agent_type=AgentType.CRUSHER, region=Region.BRAZIL
        ),
        config,
    )
    observers = tuple(
        _PriceObserver(
            AgentIdentity(
                agent_id=f"observer-{region}",
                agent_type=AgentType.TRADER,
                region=region,
            )
        )
        for region in regions
    )
    agents = (crusher, *observers)
    flows = tuple(
        MarketFlow(
            month=month,
            region=Region.BRAZIL,
            commodity=commodity,
            flow_type=flow_type,
            quantity_tonnes=quantity,
        )
        for month in (december, january)
        for commodity, flow_type, quantity in (
            (Commodity.SOYBEANS, MarketFlowType.SUPPLY, 200),
            (Commodity.SOYBEAN_MEAL, MarketFlowType.SUPPLY, 90000),
            (Commodity.SOYBEAN_MEAL, MarketFlowType.CONSUMPTION, 40),
            (Commodity.SOYBEAN_OIL, MarketFlowType.CONSUMPTION, 10),
        )
    )
    dynamics = PriceDynamicsConfig()
    original = (
        initial.model_dump_json(),
        tuple(flow.model_dump_json() for flow in flows),
        tuple(price.model_dump_json() for price in baselines),
        config.model_dump_json(),
        dynamics.model_dump_json(),
    )
    result = run_simulation(
        initial,
        agents,
        crusher_configs={"crusher-br": config},
        months=2,
        monthly_flows=iter(flows),
        regional_prices=iter(baselines),
        price_dynamics_config=dynamics,
    )
    assert [monthly.month for monthly in result.monthly_results] == [december, january]
    for observer in observers:
        expected_values = (
            (
                global_prices,
                {
                    Commodity.SOYBEANS: 225,
                    Commodity.SOYBEAN_MEAL: 270,
                    Commodity.SOYBEAN_OIL: 720,
                },
            )
            if observer.identity.region == Region.BRAZIL
            else ({commodity: value * 2 for commodity, value in global_prices.items()},)
            * 2
        )
        assert [snapshot.month for snapshot in observer.observations] == [
            december,
            january,
        ]
        for snapshot, expected in zip(
            observer.observations, expected_values, strict=True
        ):
            assert {
                price.commodity: price.price_usd_per_tonne for price in snapshot.prices
            } == pytest.approx(expected)
            assert all(price.month == snapshot.month for price in snapshot.prices)
    for monthly in result.monthly_results:
        assert len(monthly.regional_prices) == len(monthly.market_pressures) == 6
        assert all(price.month == monthly.month for price in monthly.regional_prices)
        assert all(
            pressure.month == monthly.month for pressure in monthly.market_pressures
        )
        assert [settled.executed_tonnes for settled in monthly.settlement_results] == [
            100
        ]
        assert {
            pressure.commodity: (pressure.supply_tonnes, pressure.demand_tonnes)
            for pressure in monthly.market_pressures
            if pressure.region == Region.BRAZIL
        } == {
            Commodity.SOYBEANS: (200, 100),
            Commodity.SOYBEAN_MEAL: (80, 40),
            Commodity.SOYBEAN_OIL: (20, 10),
        }
        assert all(
            pressure.supply_tonnes == pressure.demand_tonnes == 0
            for pressure in monthly.market_pressures
            if pressure.region == Region.EUROPEAN_UNION
        )
        for observer in observers:
            assert {
                price.commodity: price.price_usd_per_tonne
                for price in monthly.regional_prices
                if price.region == observer.identity.region
            } == {
                price.commodity: price.price_usd_per_tonne
                for snapshot in observer.observations
                if snapshot.month == monthly.month
                for price in snapshot.prices
            }
        for state in (monthly.starting_state, monthly.ending_state):
            assert {
                price.commodity: price.price_usd_per_tonne
                for price in state.snapshot.prices
            } == global_prices
    assert {
        stock.commodity: stock.quantity_tonnes
        for stock in result.final_state.snapshot.inventories
        if stock.region == Region.BRAZIL
    } == {
        Commodity.SOYBEANS: 1200,
        Commodity.SOYBEAN_MEAL: 180,
        Commodity.SOYBEAN_OIL: 120,
    }
    assert (
        result.monthly_results[1].starting_state.snapshot.inventories
        == result.monthly_results[0].ending_state.snapshot.inventories
    )
    assert result.initial_state == initial
    assert result.final_state == result.monthly_results[-1].ending_state
    assert SimulationResult.model_validate_json(result.model_dump_json()) == result
    assert original == (
        initial.model_dump_json(),
        tuple(flow.model_dump_json() for flow in flows),
        tuple(price.model_dump_json() for price in baselines),
        config.model_dump_json(),
        dynamics.model_dump_json(),
    )
    assert result == run_simulation(
        initial,
        reversed(agents),
        crusher_configs={"crusher-br": config},
        months=2,
        monthly_flows=reversed(flows),
        regional_prices=reversed(baselines),
        price_dynamics_config=dynamics,
    )
