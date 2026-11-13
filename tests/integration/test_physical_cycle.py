import math
from dataclasses import dataclass
from datetime import date

from soybean_sim.agents import (
    AgentDecision,
    AgentIdentity,
    AgentType,
    Crusher,
    CrusherConfig,
    DecisionAction,
)
from soybean_sim.market import (
    MarketFlow,
    MarketFlowType,
    MarketState,
    SettlementOutcome,
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


@dataclass(frozen=True)
class _TradingIntentions:
    """Supply fixed test orders so same-month output availability can be exercised."""

    identity: AgentIdentity
    action: DecisionAction
    quantities: tuple[tuple[Commodity, float], ...]

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Return intentions without executing or reserving inventory.

        Args:
            snapshot: Current-month observations after production.

        Returns:
            Validated current-month orders with fixed synthetic quantities.
        """
        return tuple(
            AgentDecision(
                agent=self.identity,
                month=snapshot.month,
                action=self.action,
                commodity=commodity,
                quantity_tonnes=quantity,
            )
            for commodity, quantity in self.quantities
        )


def test_two_region_physical_cycle() -> None:
    """Trade current-month crushing outputs before applying destination consumption.

    Returns:
        None: Confirms exact physical accounting, histories, and repeatability.
    """
    month = date(2023, 12, 1)
    global_values = {
        Commodity.SOYBEANS: 250,
        Commodity.SOYBEAN_MEAL: 300,
        Commodity.SOYBEAN_OIL: 800,
    }
    state = MarketState.from_snapshot(
        MarketSnapshot(
            month=month,
            prices=tuple(
                CommodityPrice(
                    commodity=commodity,
                    month=month,
                    price_usd_per_tonne=value,
                    data_source="synthetic physical-cycle integration",
                )
                for commodity, value in global_values.items()
            ),
            inventories=tuple(
                Inventory(
                    region=region,
                    commodity=commodity,
                    quantity_tonnes=20
                    if (region, commodity) == (Region.BRAZIL, Commodity.SOYBEANS)
                    else 0,
                )
                for region in (Region.BRAZIL, Region.EUROPEAN_UNION)
                for commodity in Commodity
            ),
        )
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
    seller = _TradingIntentions(
        AgentIdentity(
            agent_id="seller-br", agent_type=AgentType.TRADER, region=Region.BRAZIL
        ),
        DecisionAction.OFFER_FOR_SALE,
        tuple((commodity, 100) for commodity in Commodity),
    )
    buyer = _TradingIntentions(
        AgentIdentity(
            agent_id="buyer-eu",
            agent_type=AgentType.TRADER,
            region=Region.EUROPEAN_UNION,
        ),
        DecisionAction.PURCHASE,
        (
            (Commodity.SOYBEANS, 40),
            (Commodity.SOYBEAN_MEAL, 50),
            (Commodity.SOYBEAN_OIL, 10),
        ),
    )
    agents = (seller, buyer, crusher)
    regional = tuple(
        price.model_copy(
            update={"price_usd_per_tonne": price.price_usd_per_tonne * 1.1}
        )
        if price.region == Region.EUROPEAN_UNION
        else price
        for price in initialize_regional_prices(
            state.snapshot, [Region.BRAZIL, Region.EUROPEAN_UNION]
        )
    )
    flows = []
    for flow_month, soybeans in ((month, 80), (date(2024, 1, 1), 100)):
        for region, commodity, flow_type, quantity in (
            (Region.BRAZIL, Commodity.SOYBEANS, MarketFlowType.SUPPLY, soybeans),
            (Region.BRAZIL, Commodity.SOYBEAN_MEAL, MarketFlowType.SUPPLY, 10000),
            (Region.BRAZIL, Commodity.SOYBEAN_OIL, MarketFlowType.SUPPLY, 10000),
            (
                Region.EUROPEAN_UNION,
                Commodity.SOYBEAN_MEAL,
                MarketFlowType.CONSUMPTION,
                40,
            ),
            (
                Region.EUROPEAN_UNION,
                Commodity.SOYBEAN_OIL,
                MarketFlowType.CONSUMPTION,
                5,
            ),
        ):
            flows.append(
                MarketFlow(
                    month=flow_month,
                    region=region,
                    commodity=commodity,
                    flow_type=flow_type,
                    quantity_tonnes=quantity,
                )
            )
    original_state = state.model_dump_json()
    original_flows = [flow.model_dump_json() for flow in flows]
    original_prices = [price.model_dump_json() for price in regional]
    result = run_simulation(
        state,
        agents,
        crusher_configs={"crusher-br": config},
        months=2,
        regional_prices=regional,
        monthly_flows=iter(flows),
    )
    assert result.initial_state == state
    assert [item.month for item in result.monthly_results] == [month, date(2024, 1, 1)]
    for index, monthly in enumerate(result.monthly_results, start=1):
        assert monthly.physical_cycle
        assert len(monthly.market_flows) == 5
        assert {flow for flow in monthly.market_flows} == {
            flow for flow in flows if flow.month == monthly.month
        }
        assert len(monthly.decisions) == 7
        assert len(monthly.settlement_results) == 1
        (crushed,) = monthly.settlement_results
        assert crushed.outcome == SettlementOutcome.CRUSH_SETTLED
        assert crushed.executed_tonnes == 100
        assert [
            (item.commodity, item.quantity_tonnes) for item in monthly.executed_trades
        ] == [(Commodity.SOYBEAN_MEAL, 50), (Commodity.SOYBEAN_OIL, 10)]
        assert all(
            item.month == monthly.month
            and item.source_region == Region.BRAZIL
            and item.destination_region == Region.EUROPEAN_UNION
            for item in monthly.executed_trades
        )
        assert {
            (item.region, item.commodity): item.quantity_tonnes
            for item in monthly.ending_state.snapshot.inventories
        } == {
            (Region.BRAZIL, Commodity.SOYBEANS): 0,
            (Region.BRAZIL, Commodity.SOYBEAN_MEAL): index * 30,
            (Region.BRAZIL, Commodity.SOYBEAN_OIL): index * 10,
            (Region.EUROPEAN_UNION, Commodity.SOYBEANS): 0,
            (Region.EUROPEAN_UNION, Commodity.SOYBEAN_MEAL): index * 10,
            (Region.EUROPEAN_UNION, Commodity.SOYBEAN_OIL): index * 5,
        }
        for boundary in (monthly.starting_state, monthly.ending_state):
            assert {
                price.commodity: price.price_usd_per_tonne
                for price in boundary.snapshot.prices
            } == global_values
            assert all(
                price.month == monthly.month for price in boundary.snapshot.prices
            )
            assert all(
                math.isfinite(stock.quantity_tonnes) and stock.quantity_tonnes >= 0
                for stock in boundary.snapshot.inventories
            )
    assert result.monthly_results[0].starting_state == state
    assert (
        result.monthly_results[1].starting_state.snapshot.inventories
        == result.monthly_results[0].ending_state.snapshot.inventories
    )
    assert result.final_state == result.monthly_results[-1].ending_state
    assert SimulationResult.model_validate_json(result.model_dump_json()) == result
    assert state.model_dump_json() == original_state
    assert [flow.model_dump_json() for flow in flows] == original_flows
    assert [price.model_dump_json() for price in regional] == original_prices
    assert result == run_simulation(
        state,
        reversed(agents),
        crusher_configs={"crusher-br": config},
        months=2,
        regional_prices=reversed(regional),
        monthly_flows=reversed(flows),
    )
