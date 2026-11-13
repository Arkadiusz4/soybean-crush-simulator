import math
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import permutations
from typing import Any

import pytest

from soybean_sim.agents import (
    Agent,
    AgentDecision,
    AgentIdentity,
    AgentType,
    CrusherConfig,
    DecisionAction,
)
from soybean_sim.market import (
    FlowSettlementError,
    MarketFlow,
    MarketFlowType,
    MarketState,
    SettlementOutcome,
    initialize_regional_prices,
)
from soybean_sim.models import Commodity, Inventory, MarketSnapshot, Region
from soybean_sim.simulation import SimulationError, run_simulation


@dataclass
class _ScriptedAgent:
    """Return fixed test intentions and record their actual observations."""

    identity: AgentIdentity
    intentions: tuple[tuple[DecisionAction, Commodity, float], ...] = ()
    observations: list[MarketSnapshot] = field(default_factory=list)

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Capture a snapshot and produce current-month test intentions.

        Args:
            snapshot: Observations supplied by the engine.

        Returns:
            Validated decisions without executing any activity.
        """
        self.observations.append(snapshot)
        return tuple(
            AgentDecision(
                agent=self.identity,
                month=snapshot.month,
                action=action,
                commodity=commodity,
                quantity_tonnes=quantity,
            )
            for action, commodity, quantity in self.intentions
        )


@pytest.fixture
def flow(state: MarketState) -> MarketFlow:
    """Provide soybean supply in the initial simulation month.

    Args:
        state: Initial Brazilian market.

    Returns:
        Three thousand synthetic tonnes of supply.
    """
    return MarketFlow(
        month=state.snapshot.month,
        region=Region.BRAZIL,
        commodity=Commodity.SOYBEANS,
        flow_type=MarketFlowType.SUPPLY,
        quantity_tonnes=3000,
    )


@pytest.fixture
def two_region_state(state: MarketState) -> MarketState:
    """Add zero US stocks to the initial Brazilian market.

    Args:
        state: Initial synthetic state.

    Returns:
        Validated state with explicit inventories for both trading endpoints.
    """
    return MarketState.from_snapshot(
        MarketSnapshot(
            month=state.snapshot.month,
            prices=state.snapshot.prices,
            inventories=(
                *state.snapshot.inventories,
                *(
                    Inventory(
                        region=Region.UNITED_STATES,
                        commodity=commodity,
                        quantity_tonnes=0,
                    )
                    for commodity in sorted(Commodity)
                ),
            ),
        )
    )


def _stocks(state: MarketState) -> dict[tuple[Region, Commodity], float]:
    """Read regional quantities independently of inventory ordering.

    Args:
        state: Market state to inspect.

    Returns:
        Quantities keyed by regional commodity.
    """
    return {
        (item.region, item.commodity): item.quantity_tonnes
        for item in state.snapshot.inventories
    }


def test_legacy_mode_and_empty_physical_mode_are_distinct(
    two_region_state: MarketState,
) -> None:
    """Keep legacy deferral while explicitly empty flows enable physical trading.

    Args:
        two_region_state: Brazilian stock and empty US stock.

    Returns:
        None: Confirms exact legacy compatibility and opt-in trade execution.
    """
    seller = _ScriptedAgent(
        AgentIdentity(
            agent_id="seller", agent_type=AgentType.PRODUCER, region=Region.BRAZIL
        ),
        ((DecisionAction.OFFER_FOR_SALE, Commodity.SOYBEANS, 100),),
    )
    buyer = _ScriptedAgent(
        AgentIdentity(
            agent_id="buyer", agent_type=AgentType.TRADER, region=Region.UNITED_STATES
        ),
        ((DecisionAction.PURCHASE, Commodity.SOYBEANS, 50),),
    )
    agents = [seller, buyer]
    legacy = run_simulation(two_region_state, agents, crusher_configs={}, months=1)
    assert legacy == run_simulation(
        two_region_state, agents, crusher_configs={}, months=1, monthly_flows=None
    )
    monthly = legacy.monthly_results[0]
    assert monthly.market_flows == monthly.executed_trades == ()
    assert not monthly.physical_cycle
    assert len(monthly.settlement_results) == 2
    assert all(
        item.outcome == SettlementOutcome.TRADE_MATCHING_NOT_IMPLEMENTED
        for item in monthly.settlement_results
    )
    assert legacy.final_state == two_region_state
    physical = run_simulation(
        two_region_state, agents, crusher_configs={}, months=1, monthly_flows=iter(())
    )
    monthly = physical.monthly_results[0]
    assert monthly.physical_cycle
    assert monthly.market_flows == monthly.settlement_results == ()
    assert monthly.decisions == legacy.monthly_results[0].decisions
    assert len(monthly.executed_trades) == 1
    assert monthly.executed_trades[0].quantity_tonnes == 50
    expected = _stocks(two_region_state)
    expected[Region.BRAZIL, Commodity.SOYBEANS] -= 50
    expected[Region.UNITED_STATES, Commodity.SOYBEANS] += 50
    assert _stocks(physical.final_state) == expected


def test_production_is_visible_in_regional_agent_observations(
    state: MarketState, flow: MarketFlow
) -> None:
    """Apply soybean production before constructing a regional agent view.

    Args:
        state: Initial market state.
        flow: Current-month soybean supply.

    Returns:
        None: Confirms post-production observation and pre-production history boundary.
    """
    observer = _ScriptedAgent(
        AgentIdentity(
            agent_id="observer", agent_type=AgentType.TRADER, region=Region.BRAZIL
        )
    )
    prices = tuple(
        price.model_copy(update={"price_usd_per_tonne": 200})
        if price.commodity == Commodity.SOYBEANS
        else price
        for price in initialize_regional_prices(state.snapshot, [Region.BRAZIL])
    )
    original_state = state.model_dump_json()
    original_flow = flow.model_dump_json()
    result = run_simulation(
        state,
        [observer],
        crusher_configs={},
        months=1,
        regional_prices=prices,
        monthly_flows=[flow],
    )
    (observed,) = observer.observations
    assert (
        next(
            item.quantity_tonnes
            for item in observed.inventories
            if item.commodity == Commodity.SOYBEANS
        )
        == 13000
    )
    assert (
        next(
            price.price_usd_per_tonne
            for price in observed.prices
            if price.commodity == Commodity.SOYBEANS
        )
        == 200
    )
    assert observed.month == state.snapshot.month
    assert all(price.data_source == "simulated_regional" for price in observed.prices)
    monthly = result.monthly_results[0]
    assert monthly.starting_state == result.initial_state == state
    assert monthly.market_flows == (flow,)
    assert _stocks(monthly.ending_state)[Region.BRAZIL, Commodity.SOYBEANS] == 13000
    assert monthly.ending_state.snapshot.prices == state.snapshot.prices
    assert state.model_dump_json() == original_state
    assert flow.model_dump_json() == original_flow


def test_production_crushing_and_consumption_are_applied_once(
    state: MarketState,
    flow: MarketFlow,
    agents: tuple[Agent, ...],
    configs: dict[str, CrusherConfig],
) -> None:
    """Use real agents while excluding USDA reference output from physical stock.

    Args:
        state: Initial synthetic market.
        flow: Three thousand tonnes of soybean production.
        agents: Existing Producer, Crusher, and Trader instances.
        configs: Shared crusher configuration.

    Returns:
        None: Confirms exact end stocks and crushing-only settlement history.
    """
    records = [flow]
    for commodity, consumption in (
        (Commodity.SOYBEAN_MEAL, 3200),
        (Commodity.SOYBEAN_OIL, 800),
    ):
        records.append(
            flow.model_copy(update={"commodity": commodity, "quantity_tonnes": 100000})
        )
        records.append(
            flow.model_copy(
                update={
                    "commodity": commodity,
                    "flow_type": MarketFlowType.CONSUMPTION,
                    "quantity_tonnes": consumption,
                }
            )
        )
    result = run_simulation(
        state, agents, crusher_configs=configs, months=1, monthly_flows=records
    )
    assert _stocks(result.final_state) == {
        (Region.BRAZIL, Commodity.SOYBEANS): 9000,
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL): 0,
        (Region.BRAZIL, Commodity.SOYBEAN_OIL): 0,
    }
    monthly = result.monthly_results[0]
    assert len(monthly.market_flows) == 5
    assert len(monthly.decisions) == 3
    assert len(monthly.settlement_results) == 1
    assert monthly.settlement_results[0].decision.action == DecisionAction.CRUSH
    assert monthly.settlement_results[0].executed_tonnes == 4000
    assert monthly.executed_trades == ()
    assert (
        next(
            item.quantity_tonnes
            for item in monthly.decisions
            if item.agent.agent_type == AgentType.PRODUCER
        )
        == 6500
    )


def test_partial_crushing_uses_executed_quantity(state: MarketState) -> None:
    """Preserve the existing stock-limited crushing policy for oversized requests.

    Args:
        state: Ten thousand available soybean tonnes.

    Returns:
        None: Confirms products reflect actual processing rather than requested input.
    """
    crusher = _ScriptedAgent(
        AgentIdentity(
            agent_id="crusher", agent_type=AgentType.CRUSHER, region=Region.BRAZIL
        ),
        ((DecisionAction.CRUSH, Commodity.SOYBEANS, 20000),),
    )
    config = CrusherConfig(
        monthly_capacity_tonnes=20000, processing_cost_usd_per_tonne=10
    )
    result = run_simulation(
        state,
        [crusher],
        crusher_configs={"crusher": config},
        months=1,
        monthly_flows=[],
    )
    (settlement,) = result.monthly_results[0].settlement_results
    assert settlement.requested_tonnes == 20000
    assert settlement.executed_tonnes == 10000
    assert _stocks(result.final_state) == {
        (Region.BRAZIL, Commodity.SOYBEANS): 0,
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL): 8000,
        (Region.BRAZIL, Commodity.SOYBEAN_OIL): 2000,
    }


def test_months_without_flows_and_year_rollover(
    state: MarketState, flow: MarketFlow
) -> None:
    """Carry physical stocks through an empty month and December-to-January rollover.

    Args:
        state: Initial November market.
        flow: Baseline supply used to construct the schedule.

    Returns:
        None: Confirms deterministic histories, constant prices, and input preservation.
    """
    observer = _ScriptedAgent(
        AgentIdentity(
            agent_id="observer", agent_type=AgentType.TRADER, region=Region.BRAZIL
        )
    )
    records = [
        flow.model_copy(update={"quantity_tonnes": 500}),
        flow.model_copy(update={"month": date(2024, 1, 1), "quantity_tonnes": 1000}),
        flow.model_copy(
            update={
                "month": date(2024, 1, 1),
                "commodity": Commodity.SOYBEAN_MEAL,
                "quantity_tonnes": 20000,
            }
        ),
    ]
    regional = initialize_regional_prices(state.snapshot, [Region.BRAZIL])
    original_state = state.model_dump_json()
    original_flows = [item.model_dump_json() for item in records]
    result = run_simulation(
        state,
        [observer],
        crusher_configs={},
        months=3,
        regional_prices=regional,
        monthly_flows=iter(records),
    )
    assert [item.month for item in result.monthly_results] == [
        date(2023, 11, 1),
        date(2023, 12, 1),
        date(2024, 1, 1),
    ]
    assert [len(item.market_flows) for item in result.monthly_results] == [1, 0, 2]
    assert [
        _stocks(item.ending_state)[Region.BRAZIL, Commodity.SOYBEANS]
        for item in result.monthly_results
    ] == [10500, 10500, 11500]
    for monthly, observed in zip(
        result.monthly_results, observer.observations, strict=True
    ):
        assert observed.month == monthly.month
        assert all(price.month == monthly.month for price in observed.prices)
        assert {
            price.commodity: price.price_usd_per_tonne for price in observed.prices
        } == {price.commodity: price.price_usd_per_tonne for price in regional}
        assert observed.inventories == monthly.ending_state.snapshot.inventories
        assert all(
            price.month == monthly.month
            for price in monthly.ending_state.snapshot.prices
        )
        assert {
            price.commodity: (price.price_usd_per_tonne, price.data_source)
            for price in monthly.ending_state.snapshot.prices
        } == {
            price.commodity: (price.price_usd_per_tonne, price.data_source)
            for price in state.snapshot.prices
        }
    for ordering in permutations(records):
        assert (
            run_simulation(
                state,
                [observer],
                crusher_configs={},
                months=3,
                regional_prices=reversed(regional),
                monthly_flows=ordering,
            )
            == result
        )
    assert state.model_dump_json() == original_state
    assert [item.model_dump_json() for item in records] == original_flows
    assert all(
        math.isfinite(stock.quantity_tonnes) and stock.quantity_tonnes >= 0
        for stock in result.final_state.snapshot.inventories
    )


def test_unmatched_intentions_do_not_change_inventory(state: MarketState) -> None:
    """Keep same-region trade intentions in history without claiming executions.

    Args:
        state: Initial Brazilian state.

    Returns:
        None: Confirms unmatched decisions have no inventory or settlement effects.
    """
    seller = _ScriptedAgent(
        AgentIdentity(
            agent_id="seller", agent_type=AgentType.TRADER, region=Region.BRAZIL
        ),
        ((DecisionAction.OFFER_FOR_SALE, Commodity.SOYBEANS, 100),),
    )
    buyer = _ScriptedAgent(
        AgentIdentity(
            agent_id="buyer", agent_type=AgentType.TRADER, region=Region.BRAZIL
        ),
        ((DecisionAction.PURCHASE, Commodity.SOYBEANS, 100),),
    )
    result = run_simulation(
        state, [seller, buyer], crusher_configs={}, months=1, monthly_flows=[]
    )
    monthly = result.monthly_results[0]
    assert len(monthly.decisions) == 2
    assert monthly.settlement_results == monthly.executed_trades == ()
    assert result.final_state == state


@pytest.mark.parametrize("conflicting", [False, True])
@pytest.mark.parametrize("reference", [False, True])
def test_duplicate_flows_are_rejected_before_decisions(
    state: MarketState, flow: MarketFlow, conflicting: bool, reference: bool
) -> None:
    """Validate duplicate schedule keys before any month executes.

    Args:
        state: Initial market state.
        flow: Baseline supply.
        conflicting: Whether repeated quantities differ.
        reference: Whether the record is reference-only product supply.

    Returns:
        None: Confirms identical and conflicting duplicates both fail fast.
    """
    observer = _ScriptedAgent(
        AgentIdentity(
            agent_id="observer", agent_type=AgentType.TRADER, region=Region.BRAZIL
        )
    )
    first = (
        flow.model_copy(update={"commodity": Commodity.SOYBEAN_MEAL})
        if reference
        else flow
    )
    second = first.model_copy(update={"quantity_tonnes": 1} if conflicting else {})
    with pytest.raises(SimulationError, match="Duplicate monthly flow"):
        run_simulation(
            state,
            [observer],
            crusher_configs={},
            months=2,
            monthly_flows=[first, second],
        )
    assert observer.observations == []


@pytest.mark.parametrize(
    "changes",
    [
        {"month": date(2023, 10, 1)},
        {"month": date(2024, 2, 1)},
        {"month": date(2023, 11, 2)},
        {"month": datetime(2023, 11, 1)},
        {"month": "2023-11-01"},
        {"region": Region.EUROPEAN_UNION},
        {"region": "unknown"},
        {"commodity": "unknown"},
        {"flow_type": "unknown"},
        {"flow_type": MarketFlowType.CONSUMPTION},
        {"quantity_tonnes": -1},
        {"quantity_tonnes": float("nan")},
        {"quantity_tonnes": float("inf")},
        {"quantity_tonnes": True},
        {"quantity_tonnes": "3000"},
    ],
)
def test_entire_flow_collection_is_validated_upfront(
    state: MarketState, flow: MarketFlow, changes: dict[str, object]
) -> None:
    """Reject malformed or out-of-period records even after a valid early flow.

    Args:
        state: Initial market state.
        flow: Valid initial flow.
        changes: Invalid fields copied into a later supplied record.

    Returns:
        None: Confirms no agent observes a partial simulation.
    """
    observer = _ScriptedAgent(
        AgentIdentity(
            agent_id="observer", agent_type=AgentType.TRADER, region=Region.BRAZIL
        )
    )
    original = state.model_dump_json()
    with pytest.raises(SimulationError):
        run_simulation(
            state,
            [observer],
            crusher_configs={},
            months=3,
            monthly_flows=iter([flow, flow.model_copy(update=changes)]),
        )
    assert observer.observations == []
    assert state.model_dump_json() == original


@pytest.mark.parametrize("records", [42, [{}], ["invalid"]])
def test_invalid_flow_collection_types(state: MarketState, records: Any) -> None:
    """Require an iterable of typed flow records when opting into the physical cycle.

    Args:
        state: Initial state.
        records: Malformed collection or elements.

    Returns:
        None: Confirms typed schedule validation uses SimulationError.
    """
    with pytest.raises(SimulationError, match="monthly_flows"):
        run_simulation(state, [], crusher_configs={}, months=1, monthly_flows=records)


def test_missing_flow_inventory_is_rejected_before_decisions(
    state: MarketState, flow: MarketFlow
) -> None:
    """Require explicit product stock records even for reference-only supply.

    Args:
        state: Initial market state.
        flow: Baseline supply.

    Returns:
        None: Confirms settlement cannot silently create missing inventories.
    """
    initial = MarketState.from_snapshot(
        MarketSnapshot(
            month=state.snapshot.month,
            prices=state.snapshot.prices,
            inventories=tuple(
                stock
                for stock in state.snapshot.inventories
                if stock.commodity != Commodity.SOYBEAN_MEAL
            ),
        )
    )
    with pytest.raises(SimulationError, match="not represented"):
        run_simulation(
            initial,
            [],
            crusher_configs={},
            months=1,
            monthly_flows=[
                flow.model_copy(update={"commodity": Commodity.SOYBEAN_MEAL})
            ],
        )


def test_consumption_shortage_propagates_without_partial_results(
    state: MarketState,
    flow: MarketFlow,
    agents: tuple[Agent, ...],
    configs: dict[str, CrusherConfig],
) -> None:
    """Check product availability after actual crushing without clamping consumption.

    Args:
        state: Initial state with no meal stock.
        flow: Baseline supply used as a consumption template.
        agents: Concrete agents producing 3200 meal tonnes through crushing.
        configs: Shared crushing settings.

    Returns:
        None: Confirms unmet consumption propagates the settlement domain error.
    """
    consumption = flow.model_copy(
        update={
            "commodity": Commodity.SOYBEAN_MEAL,
            "flow_type": MarketFlowType.CONSUMPTION,
            "quantity_tonnes": 3201,
        }
    )
    original = state.model_dump_json()
    with pytest.raises(
        FlowSettlementError, match=r"Insufficient inventory.*brazil/soybean_meal"
    ):
        run_simulation(
            state,
            agents,
            crusher_configs=configs,
            months=1,
            monthly_flows=[consumption],
        )
    assert state.model_dump_json() == original
