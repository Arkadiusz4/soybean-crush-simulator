from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from typing import Any, cast

import pytest
from pydantic import ValidationError

from soybean_sim.agents import (
    Agent,
    AgentDecision,
    AgentIdentity,
    AgentType,
    Crusher,
    CrusherConfig,
    DecisionAction,
    ProducerDecisionError,
)
from soybean_sim.market import (
    MarketState,
    RegionalPrice,
    RegionalPriceError,
    SettlementError,
    SettlementOutcome,
    SettlementResult,
    initialize_regional_prices,
    settle_decisions,
)
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketSnapshot,
    Region,
)
from soybean_sim.simulation import (
    MonthlyResult,
    SimulationError,
    SimulationResult,
    _calendar,
    _engine,
    _models,
    next_month,
    run_simulation,
)


@dataclass
class _RecordingAgent:
    """Record observations to verify invocation order and shared monthly snapshots."""

    identity: AgentIdentity
    calls: list[tuple[str, MarketSnapshot]] = field(default_factory=list)

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Record the supplied starting observation and abstain from decisions."""
        self.calls.append((self.identity.agent_id, snapshot))
        return ()


@dataclass(frozen=True)
class _FixedDecisionAgent:
    """Return test decisions for attribution and error propagation checks."""

    identity: AgentIdentity
    decisions: tuple[AgentDecision, ...] = ()

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Return fixed test intentions without altering the observed state."""
        return self.decisions


def _redate(state: MarketState, month: date) -> MarketState:
    """Create synthetic starting observations in a different calendar month."""
    return MarketState.from_snapshot(
        MarketSnapshot(
            month=month,
            prices=tuple(
                CommodityPrice(
                    commodity=price.commodity,
                    month=month,
                    price_usd_per_tonne=price.price_usd_per_tonne,
                    data_source=price.data_source,
                )
                for price in state.snapshot.prices
            ),
            inventories=state.snapshot.inventories,
        )
    )


def test_public_exports() -> None:
    """Expose engine execution, calendar advancement, and frozen history models."""
    assert run_simulation is _engine.run_simulation
    assert SimulationError is _engine.SimulationError
    assert next_month is _calendar.next_month
    assert MonthlyResult is _models.MonthlyResult
    assert SimulationResult is _models.SimulationResult


def test_one_month_integrates_concrete_agents(
    state: MarketState, agents: tuple[Agent, ...], configs: dict[str, CrusherConfig]
) -> None:
    """Collect initial intentions and execute crushing once in the initial month."""
    result = run_simulation(state, agents, crusher_configs=configs, months=1)
    assert len(result.monthly_results) == 1
    monthly = result.monthly_results[0]
    assert monthly.month == state.snapshot.month
    assert monthly.starting_state == result.initial_state == state
    assert monthly.decisions == tuple(
        decision
        for agent in sorted(agents, key=lambda agent: agent.identity.agent_id)
        for decision in agent.decide(state.snapshot)
    )
    expected_state, settlements = settle_decisions(
        state, monthly.decisions, crusher_configs=configs
    )
    assert monthly.ending_state == result.final_state == expected_state
    assert monthly.settlement_results == settlements
    assert {
        stock.commodity: stock.quantity_tonnes
        for stock in result.final_state.snapshot.inventories
    } == {
        Commodity.SOYBEANS: 6000,
        Commodity.SOYBEAN_MEAL: 3200,
        Commodity.SOYBEAN_OIL: 800,
    }


def test_monthly_inventory_carryover(
    state: MarketState, agents: tuple[Agent, ...], configs: dict[str, CrusherConfig]
) -> None:
    """Carry stocks forward while purchase intentions remain unexecuted."""
    result = run_simulation(state, agents, crusher_configs=configs, months=4)
    history = result.monthly_results
    assert [monthly.month for monthly in history] == [
        date(2023, 11, 1),
        date(2023, 12, 1),
        date(2024, 1, 1),
        date(2024, 2, 1),
    ]
    crushed = [
        sum(item.executed_tonnes for item in monthly.settlement_results)
        for monthly in history
    ]
    assert crushed == [4000, 4000, 2000, 0]
    offers = [
        [
            item.quantity_tonnes
            for item in monthly.decisions
            if item.agent.agent_type == AgentType.PRODUCER
        ]
        for monthly in history
    ]
    assert offers == [[5000], [3000], [1000], []]
    for previous, current in pairwise(history):
        assert (
            current.starting_state.snapshot.inventories
            == previous.ending_state.snapshot.inventories
        )
    assert result.final_state == history[-1].ending_state
    assert result.final_state.snapshot.month == date(2024, 2, 1)
    assert {
        stock.commodity: stock.quantity_tonnes
        for stock in result.final_state.snapshot.inventories
    } == {
        Commodity.SOYBEANS: 0,
        Commodity.SOYBEAN_MEAL: 8000,
        Commodity.SOYBEAN_OIL: 2000,
    }
    trades = [
        item
        for monthly in history
        for item in monthly.settlement_results
        if item.decision.action != DecisionAction.CRUSH
    ]
    assert trades
    assert all(
        item.executed_tonnes == 0 and item.unfulfilled_tonnes == item.requested_tonnes
        for item in trades
    )
    assert all(
        item.outcome is SettlementOutcome.TRADE_MATCHING_NOT_IMPLEMENTED
        for item in trades
    )


def test_prices_keep_values_and_sources_with_updated_dates(
    state: MarketState, agents: tuple[Agent, ...], configs: dict[str, CrusherConfig]
) -> None:
    """Redate constant prices each month without mutating originals."""
    original = state.model_dump()
    result = run_simulation(state, agents, crusher_configs=configs, months=3)
    expected_prices = {
        price.commodity: (price.price_usd_per_tonne, price.data_source)
        for price in state.snapshot.prices
    }
    for monthly in result.monthly_results:
        assert all(
            price.month == monthly.month
            for price in monthly.starting_state.snapshot.prices
        )
        assert {
            price.commodity: (price.price_usd_per_tonne, price.data_source)
            for price in monthly.ending_state.snapshot.prices
        } == expected_prices
    first, second = result.monthly_results[:2]
    assert all(
        previous is not current
        for previous, current in zip(
            first.ending_state.snapshot.prices,
            second.starting_state.snapshot.prices,
            strict=True,
        )
    )
    assert state.model_dump() == original


def test_reordered_one_pass_agents_are_deterministic(
    state: MarketState, agents: tuple[Agent, ...], configs: dict[str, CrusherConfig]
) -> None:
    """Accept one-pass registrations without changing deterministic histories."""
    expected = run_simulation(state, agents, crusher_configs=configs, months=4)
    assert (
        run_simulation(state, reversed(agents), crusher_configs=configs, months=4)
        == expected
    )
    assert (
        run_simulation(state, iter(agents), crusher_configs=configs, months=4)
        == expected
    )


def test_agents_observe_one_starting_snapshot_per_month(state: MarketState) -> None:
    """Invoke agents in ID order using a shared starting snapshot."""
    calls: list[tuple[str, MarketSnapshot]] = []
    first = _RecordingAgent(
        AgentIdentity(agent_id="a", agent_type=AgentType.TRADER, region=Region.BRAZIL),
        calls,
    )
    second = _RecordingAgent(
        AgentIdentity(
            agent_id="z", agent_type=AgentType.PRODUCER, region=Region.BRAZIL
        ),
        calls,
    )
    run_simulation(state, [second, first], crusher_configs={}, months=2)
    assert [(agent_id, snapshot.month) for agent_id, snapshot in calls] == [
        ("a", date(2023, 11, 1)),
        ("z", date(2023, 11, 1)),
        ("a", date(2023, 12, 1)),
        ("z", date(2023, 12, 1)),
    ]
    assert calls[0][1] is calls[1][1]
    assert calls[2][1] is calls[3][1]


def test_settlement_called_once_per_month(
    state: MarketState,
    agents: tuple[Agent, ...],
    configs: dict[str, CrusherConfig],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass all decisions together to the existing physical settlement function."""
    calls: list[tuple[MarketState, tuple[AgentDecision, ...]]] = []

    def record_settlement(
        state: MarketState,
        decisions: Iterable[AgentDecision],
        *,
        crusher_configs: Mapping[str, CrusherConfig],
    ) -> tuple[MarketState, tuple[SettlementResult, ...]]:
        """Record one batch and delegate its actual physical processing."""
        batch = tuple(decisions)
        calls.append((state, batch))
        return settle_decisions(state, batch, crusher_configs=crusher_configs)

    monkeypatch.setattr(_engine, "settle_decisions", record_settlement)
    result = run_simulation(state, agents, crusher_configs=configs, months=3)
    assert len(calls) == 3
    assert [batch for _, batch in calls] == [
        monthly.decisions for monthly in result.monthly_results
    ]


def test_empty_agents_advance_calendar_without_physical_changes(
    state: MarketState,
) -> None:
    """Record requested months even when no agent submits an intention."""
    result = run_simulation(state, [], crusher_configs={}, months=15)
    assert len(result.monthly_results) == 15
    assert result.final_state.snapshot.month == date(2025, 1, 1)
    assert all(
        monthly.decisions == monthly.settlement_results == ()
        for monthly in result.monthly_results
    )
    assert result.final_state.snapshot.inventories == state.snapshot.inventories


@pytest.mark.parametrize("months", [0, -1, True, False, 1.5, "2", None])
def test_invalid_month_count(state: MarketState, months: Any) -> None:
    """Reject nonpositive durations and implicit numeric coercion."""
    with pytest.raises(SimulationError, match="months must be a positive integer"):
        run_simulation(state, [], crusher_configs={}, months=months)


def test_calendar_horizon_and_last_month(state: MarketState) -> None:
    """Allow a last December result without advancing the final state beyond it."""
    last = _redate(state, date(9999, 12, 1))
    result = run_simulation(last, [], crusher_configs={}, months=1)
    assert result.final_state.snapshot.month == date(9999, 12, 1)
    with pytest.raises(SimulationError, match="maximum supported calendar year"):
        run_simulation(last, [], crusher_configs={}, months=2)


def test_duplicate_agent_ids(
    state: MarketState, agents: tuple[Agent, ...], configs: dict[str, CrusherConfig]
) -> None:
    """Reject repeated registrations even when they reference the same agent object."""
    with pytest.raises(SimulationError, match="Duplicate agent ID"):
        run_simulation(state, [agents[0], agents[0]], crusher_configs=configs, months=1)


def test_agent_region_must_be_represented(state: MarketState) -> None:
    """Reject agents outside the regions described by current inventory records."""
    agent = _FixedDecisionAgent(
        AgentIdentity(
            agent_id="unknown-region",
            agent_type=AgentType.TRADER,
            region=Region.ARGENTINA,
        )
    )
    with pytest.raises(SimulationError, match="region argentina is not represented"):
        run_simulation(state, [agent], crusher_configs={}, months=1)


@pytest.mark.parametrize("identity", [None, "agent-1"])
def test_identity_model_is_required(state: MarketState, identity: Any) -> None:
    """Require a typed identity rather than inventing one from an arbitrary value."""
    with pytest.raises(SimulationError, match="must expose an AgentIdentity"):
        run_simulation(
            state, [_FixedDecisionAgent(identity)], crusher_configs={}, months=1
        )


def test_identity_fields_are_validated(state: MarketState) -> None:
    """Reject invalid identities copied without invoking the Pydantic validators."""
    identity = AgentIdentity(
        agent_id="valid", agent_type=AgentType.TRADER, region=Region.BRAZIL
    ).model_copy(update={"agent_id": ""})
    with pytest.raises(ValidationError, match="agent_id"):
        run_simulation(
            state, [_FixedDecisionAgent(identity)], crusher_configs={}, months=1
        )


def test_missing_crusher_config_even_for_abstention(state: MarketState) -> None:
    """Require processing parameters before invoking any registered crusher."""
    agent = _FixedDecisionAgent(
        AgentIdentity(
            agent_id="crusher", agent_type=AgentType.CRUSHER, region=Region.BRAZIL
        )
    )
    with pytest.raises(
        SimulationError, match=r"Missing processing configuration.*crusher"
    ):
        run_simulation(state, [agent], crusher_configs={}, months=1)


def test_invalid_crusher_configuration(
    state: MarketState, agents: tuple[Agent, ...]
) -> None:
    """Reject an unvalidated parameter mapping at the CrusherConfig boundary."""
    configs: dict[str, Any] = {"crusher-br": {"meal_yield_fraction": 0.8}}
    with pytest.raises(SimulationError, match="expected a CrusherConfig"):
        run_simulation(state, agents, crusher_configs=configs, months=1)


def test_matching_concrete_crusher_configurations(
    state: MarketState, configs: dict[str, CrusherConfig]
) -> None:
    """Accept separate configuration instances with equal decision parameters."""
    config = configs["crusher-br"].model_copy()
    assert config is not configs["crusher-br"]
    crusher = Crusher(
        identity=AgentIdentity(
            agent_id="crusher-br", agent_type=AgentType.CRUSHER, region=Region.BRAZIL
        ),
        config=config,
    )
    result = run_simulation(state, [crusher], crusher_configs=configs, months=1)
    monthly = result.monthly_results[0]
    assert monthly.decisions[0].quantity_tonnes == 4000
    assert monthly.settlement_results[0].executed_tonnes == 4000


@pytest.mark.parametrize(
    "changes",
    [
        {"monthly_capacity_tonnes": 2000},
        {"processing_cost_usd_per_tonne": 20},
        {"meal_yield_fraction": 0.7, "oil_yield_fraction": 0.3},
        {"high_margin_threshold_usd_per_tonne": 40},
        {"high_utilization_fraction": 0.9},
    ],
)
def test_mismatching_crusher_configurations_fail_before_decisions(
    state: MarketState,
    configs: dict[str, CrusherConfig],
    changes: dict[str, float],
) -> None:
    """Reject differing parameters before any agent observes a simulation month."""
    observer = _RecordingAgent(
        AgentIdentity(
            agent_id="a-observer", agent_type=AgentType.PRODUCER, region=Region.BRAZIL
        )
    )
    crusher = Crusher(
        identity=AgentIdentity(
            agent_id="crusher-br", agent_type=AgentType.CRUSHER, region=Region.BRAZIL
        ),
        config=configs["crusher-br"],
    )
    settlement_config = CrusherConfig.model_validate(
        configs["crusher-br"].model_dump() | changes
    )
    with pytest.raises(
        SimulationError, match=r"crusher-br.*does not match.*crusher_configs"
    ):
        run_simulation(
            state,
            [observer, crusher],
            crusher_configs={"crusher-br": settlement_config},
            months=2,
        )
    assert observer.calls == []


def test_protocol_crusher_without_configuration_attribute(
    state: MarketState, configs: dict[str, CrusherConfig]
) -> None:
    """Keep custom Protocol agents compatible with external settlement parameters."""
    crusher = _RecordingAgent(
        AgentIdentity(
            agent_id="crusher-br", agent_type=AgentType.CRUSHER, region=Region.BRAZIL
        )
    )
    result = run_simulation(state, [crusher], crusher_configs=configs, months=1)
    assert crusher.calls == [("crusher-br", state.snapshot)]
    assert result.final_state == state


def test_initial_state_is_revalidated(state: MarketState) -> None:
    """Reject an invalid copied initial snapshot before any monthly invocation."""
    initial = state.model_copy(
        update={
            "snapshot": state.snapshot.model_copy(update={"month": date(2023, 11, 2)})
        }
    )
    with pytest.raises(ValidationError, match="month"):
        run_simulation(initial, [], crusher_configs={}, months=1)


def test_agent_domain_errors_propagate(
    state: MarketState, agents: tuple[Agent, ...]
) -> None:
    """Preserve the concrete producer's missing-price error without suppressing it."""
    producer = next(
        agent for agent in agents if agent.identity.agent_type == AgentType.PRODUCER
    )
    initial = MarketState.from_snapshot(
        MarketSnapshot(
            month=state.snapshot.month,
            prices=tuple(
                price
                for price in state.snapshot.prices
                if price.commodity != Commodity.SOYBEANS
            ),
            inventories=state.snapshot.inventories,
        )
    )
    with pytest.raises(ProducerDecisionError, match="missing global soybean price"):
        run_simulation(initial, [producer], crusher_configs={}, months=2)


def test_settlement_domain_errors_propagate(state: MarketState) -> None:
    """Propagate settlement errors for decisions from another month."""
    identity = AgentIdentity(
        agent_id="trader", agent_type=AgentType.TRADER, region=Region.BRAZIL
    )
    intention = AgentDecision(
        agent=identity,
        month=date(2023, 12, 1),
        action=DecisionAction.PURCHASE,
        commodity=Commodity.SOYBEANS,
        quantity_tonnes=1,
    )
    with pytest.raises(SettlementError, match="does not match market month"):
        run_simulation(
            state,
            [_FixedDecisionAgent(identity, (intention,))],
            crusher_configs={},
            months=1,
        )


def test_decisions_must_belong_to_registered_agent(state: MarketState) -> None:
    """Reject identity substitution before intentions reach settlement."""
    identity = AgentIdentity(
        agent_id="trader", agent_type=AgentType.TRADER, region=Region.BRAZIL
    )
    other = identity.model_copy(update={"agent_id": "another-trader"})
    intention = AgentDecision(
        agent=other,
        month=state.snapshot.month,
        action=DecisionAction.PURCHASE,
        commodity=Commodity.SOYBEANS,
        quantity_tonnes=1,
    )
    with pytest.raises(SimulationError, match="decision for another identity"):
        run_simulation(
            state,
            [_FixedDecisionAgent(identity, (intention,))],
            crusher_configs={},
            months=1,
        )


def test_invalid_returned_decision_type(state: MarketState) -> None:
    """Report malformed intention types as agent contract violations."""
    identity = AgentIdentity(
        agent_id="trader", agent_type=AgentType.TRADER, region=Region.BRAZIL
    )
    agent = _FixedDecisionAgent(identity, (cast(AgentDecision, "invalid"),))
    with pytest.raises(SimulationError, match="invalid decision type"):
        run_simulation(state, [agent], crusher_configs={}, months=1)


@pytest.fixture
def regional_prices(state: MarketState) -> tuple[RegionalPrice, ...]:
    """Provide initial Brazilian prices equal to the global references.

    Args:
        state: Initial synthetic market observations.

    Returns:
        Regional observations for each initial commodity.
    """
    return initialize_regional_prices(state.snapshot, [Region.BRAZIL])


@pytest.fixture
def two_region_state(state: MarketState) -> MarketState:
    """Add explicit US stocks for regional observation tests.

    Args:
        state: Synthetic Brazilian market state.

    Returns:
        Validated state with both regions' inventories.
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
                        commodity=item.commodity,
                        quantity_tonnes=item.quantity_tonnes,
                    )
                    for item in state.snapshot.inventories
                ),
            ),
        )
    )


def test_explicit_none_keeps_shared_global_observations(state: MarketState) -> None:
    """Keep the existing snapshot path when regional pricing is disabled.

    Args:
        state: Initial synthetic market state.

    Returns:
        None: Confirms agents receive the stored global snapshot.
    """
    observer = _RecordingAgent(
        AgentIdentity(
            agent_id="observer", agent_type=AgentType.TRADER, region=Region.BRAZIL
        )
    )
    result = run_simulation(
        state, [observer], crusher_configs={}, months=2, regional_prices=None
    )
    for monthly, (_, observed) in zip(
        result.monthly_results, observer.calls, strict=True
    ):
        assert observed is monthly.starting_state.snapshot
    assert result == run_simulation(state, [observer], crusher_configs={}, months=2)


def test_baseline_regional_prices_preserve_concrete_simulation_results(
    state: MarketState,
    agents: tuple[Agent, ...],
    configs: dict[str, CrusherConfig],
    regional_prices: tuple[RegionalPrice, ...],
) -> None:
    """Retain existing decisions and settlement when regional values equal baseline.

    Args:
        state: Initial synthetic state.
        agents: Producer, Crusher, and Trader instances.
        configs: Shared crushing settings.
        regional_prices: Baseline regional values.

    Returns:
        None: Confirms compatibility with all concrete agent roles.
    """
    assert run_simulation(
        state,
        agents,
        crusher_configs=configs,
        months=4,
        regional_prices=regional_prices,
    ) == run_simulation(state, agents, crusher_configs=configs, months=4)


def test_agent_regions_receive_their_own_price_views(
    two_region_state: MarketState,
) -> None:
    """Share inventories while selecting prices by each registered identity's region.

    Args:
        two_region_state: Initial stocks and prices for Brazil and the US.

    Returns:
        None: Confirms regional values, agent order, and unchanged market records.
    """
    calls: list[tuple[str, MarketSnapshot]] = []
    observers = [
        _RecordingAgent(
            AgentIdentity(
                agent_id=agent_id, agent_type=AgentType.TRADER, region=region
            ),
            calls,
        )
        for agent_id, region in (
            ("z-us", Region.UNITED_STATES),
            ("a-br", Region.BRAZIL),
            ("b-br", Region.BRAZIL),
        )
    ]
    prices = tuple(
        price.model_copy(
            update={
                "price_usd_per_tonne": price.price_usd_per_tonne
                * (0.8 if price.region == Region.BRAZIL else 1.2)
            }
        )
        for price in initialize_regional_prices(
            two_region_state.snapshot, [Region.BRAZIL, Region.UNITED_STATES]
        )
    )
    original_state = two_region_state.model_dump_json()
    original_prices = [price.model_dump_json() for price in prices]
    result = run_simulation(
        two_region_state,
        observers,
        crusher_configs={},
        months=1,
        regional_prices=prices,
    )
    assert [agent_id for agent_id, _ in calls] == ["a-br", "b-br", "z-us"]
    observations = dict(calls)
    assert observations["a-br"] == observations["b-br"]
    assert observations["a-br"].prices != observations["z-us"].prices
    for agent_id, region in (
        ("a-br", Region.BRAZIL),
        ("b-br", Region.BRAZIL),
        ("z-us", Region.UNITED_STATES),
    ):
        observation = observations[agent_id]
        assert {
            price.commodity: price.price_usd_per_tonne for price in observation.prices
        } == {
            price.commodity: price.price_usd_per_tonne
            for price in prices
            if price.region == region
        }
        assert all(
            price.data_source == "simulated_regional" for price in observation.prices
        )
        assert observation.inventories == two_region_state.snapshot.inventories
        assert observation.month == two_region_state.snapshot.month
    assert result.final_state == two_region_state
    assert (
        result.monthly_results[0].starting_state.snapshot.prices
        == two_region_state.snapshot.prices
    )
    assert two_region_state.model_dump_json() == original_state
    assert [price.model_dump_json() for price in prices] == original_prices


def test_monthly_regional_views_follow_current_stocks_and_calendar(
    state: MarketState,
    agents: tuple[Agent, ...],
    configs: dict[str, CrusherConfig],
    regional_prices: tuple[RegionalPrice, ...],
) -> None:
    """Carry constant local values through rollover while exposing current stocks.

    Args:
        state: Initial November state.
        agents: Concrete regional agents.
        configs: Shared crushing settings.
        regional_prices: Initial Brazilian prices.

    Returns:
        None: Confirms monthly view dates, stock carryover, and global price isolation.
    """
    prices = tuple(
        price.model_copy(update={"price_usd_per_tonne": 200})
        if price.commodity == Commodity.SOYBEANS
        else price
        for price in regional_prices
    )
    extra = RegionalPrice(
        month=state.snapshot.month,
        region=Region.UNITED_STATES,
        commodity=Commodity.SOYBEANS,
        price_usd_per_tonne=350,
    )
    supplied = (*prices, extra)
    observer = _RecordingAgent(
        AgentIdentity(
            agent_id="zz-observer", agent_type=AgentType.TRADER, region=Region.BRAZIL
        )
    )
    original_prices = [price.model_dump_json() for price in supplied]
    original_state = state.model_dump_json()
    result = run_simulation(
        state,
        (*agents, observer),
        crusher_configs=configs,
        months=4,
        regional_prices=iter(supplied),
    )
    assert [observed.month for _, observed in observer.calls] == [
        date(2023, 11, 1),
        date(2023, 12, 1),
        date(2024, 1, 1),
        date(2024, 2, 1),
    ]
    for monthly, (_, observed) in zip(
        result.monthly_results, observer.calls, strict=True
    ):
        assert observed.inventories == monthly.starting_state.snapshot.inventories
        assert {
            price.commodity: price.price_usd_per_tonne for price in observed.prices
        } == {price.commodity: price.price_usd_per_tonne for price in prices}
        assert all(price.month == monthly.month for price in observed.prices)
        for market_state in (monthly.starting_state, monthly.ending_state):
            assert {
                price.commodity: (price.price_usd_per_tonne, price.data_source)
                for price in market_state.snapshot.prices
            } == {
                price.commodity: (price.price_usd_per_tonne, price.data_source)
                for price in state.snapshot.prices
            }
            assert all(
                price.month == monthly.month for price in market_state.snapshot.prices
            )
    observed_soybeans = [
        next(
            stock.quantity_tonnes
            for stock in observed.inventories
            if stock.commodity == Commodity.SOYBEANS
        )
        for _, observed in observer.calls
    ]
    assert observed_soybeans == [10000, 6000, 2000, 0]
    first = result.monthly_results[0]
    assert (
        next(
            decision.quantity_tonnes
            for decision in first.decisions
            if decision.agent.agent_type == AgentType.PRODUCER
        )
        == 2000
    )
    assert (
        next(
            decision.quantity_tonnes
            for decision in first.decisions
            if decision.action == DecisionAction.PURCHASE
        )
        == 1000
    )
    assert state.model_dump_json() == original_state
    assert [price.model_dump_json() for price in supplied] == original_prices
    assert result == run_simulation(
        state,
        reversed((*agents, observer)),
        crusher_configs=configs,
        months=4,
        regional_prices=reversed(supplied),
    )


def test_all_concrete_roles_respond_to_explicit_regional_prices(
    state: MarketState,
    agents: tuple[Agent, ...],
    configs: dict[str, CrusherConfig],
    regional_prices: tuple[RegionalPrice, ...],
) -> None:
    """Drive each existing role through its regional observation without agent changes.

    Args:
        state: Initial market state.
        agents: Producer, Crusher, and Trader instances.
        configs: Shared crushing settings.
        regional_prices: Baseline regional observations.

    Returns:
        None: Confirms changed offers and demand, with unprofitable crushing skipped.
    """
    starting = MarketState.from_snapshot(
        MarketSnapshot(
            month=state.snapshot.month,
            prices=state.snapshot.prices,
            inventories=tuple(
                Inventory(
                    region=item.region,
                    commodity=item.commodity,
                    quantity_tonnes=100
                    if item.commodity != Commodity.SOYBEANS
                    else item.quantity_tonnes,
                )
                for item in state.snapshot.inventories
            ),
        )
    )
    values = {
        Commodity.SOYBEANS: 400,
        Commodity.SOYBEAN_MEAL: 200,
        Commodity.SOYBEAN_OIL: 600,
    }
    prices = tuple(
        price.model_copy(update={"price_usd_per_tonne": values[price.commodity]})
        for price in regional_prices
    )
    result = run_simulation(
        starting, agents, crusher_configs=configs, months=1, regional_prices=prices
    )
    assert {
        (
            decision.agent.agent_type,
            decision.action,
            decision.commodity,
        ): decision.quantity_tonnes
        for decision in result.monthly_results[0].decisions
    } == {
        (AgentType.PRODUCER, DecisionAction.OFFER_FOR_SALE, Commodity.SOYBEANS): 8000,
        (AgentType.TRADER, DecisionAction.PURCHASE, Commodity.SOYBEANS): 200,
        (AgentType.TRADER, DecisionAction.OFFER_FOR_SALE, Commodity.SOYBEAN_MEAL): 20,
        (AgentType.TRADER, DecisionAction.OFFER_FOR_SALE, Commodity.SOYBEAN_OIL): 20,
    }
    assert result.final_state == starting
    assert all(
        settlement.executed_tonnes == 0
        for settlement in result.monthly_results[0].settlement_results
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"month": date(2023, 12, 1)},
        {"month": date(2023, 11, 2)},
        {"month": "2023-11-01"},
        {"price_usd_per_tonne": 0},
        {"price_usd_per_tonne": -1},
        {"price_usd_per_tonne": float("nan")},
        {"price_usd_per_tonne": float("inf")},
        {"price_usd_per_tonne": True},
        {"price_usd_per_tonne": "250"},
        {"region": "unknown"},
    ],
)
def test_regional_validation_finishes_before_agent_execution(
    state: MarketState,
    regional_prices: tuple[RegionalPrice, ...],
    changes: dict[str, object],
) -> None:
    """Reject malformed supplied regional data before any agent observes a month.

    Args:
        state: Initial global observations.
        regional_prices: Valid baseline regional observations.
        changes: Invalid copied fields.

    Returns:
        None: Confirms regional errors propagate with no decisions executed.
    """
    observer = _RecordingAgent(
        AgentIdentity(
            agent_id="observer", agent_type=AgentType.TRADER, region=Region.BRAZIL
        )
    )
    prices = (regional_prices[0].model_copy(update=changes), *regional_prices[1:])
    with pytest.raises(RegionalPriceError):
        run_simulation(
            state, [observer], crusher_configs={}, months=2, regional_prices=prices
        )
    assert observer.calls == []


@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_regional_records_are_rejected(
    state: MarketState,
    regional_prices: tuple[RegionalPrice, ...],
    conflicting: bool,
) -> None:
    """Reject equal and conflicting repeated regional commodity keys.

    Args:
        state: Initial market state.
        regional_prices: Valid initial series.
        conflicting: Whether the repeated record has another price value.

    Returns:
        None: Confirms duplicate validation occurs before simulation.
    """
    duplicate = regional_prices[0].model_copy(
        update={"price_usd_per_tonne": 999} if conflicting else {}
    )
    with pytest.raises(RegionalPriceError, match="Duplicate regional price"):
        run_simulation(
            state,
            [],
            crusher_configs={},
            months=1,
            regional_prices=(*regional_prices, duplicate),
        )


def test_coverage_is_required_for_every_agent_region_before_execution(
    two_region_state: MarketState,
    regional_prices: tuple[RegionalPrice, ...],
) -> None:
    """Detect a later agent's absent region before an earlier valid agent decides.

    Args:
        two_region_state: State with inventories in Brazil and the US.
        regional_prices: Complete prices for Brazil only.

    Returns:
        None: Confirms no global fallback or partial month execution.
    """
    first = _RecordingAgent(
        AgentIdentity(agent_id="a", agent_type=AgentType.TRADER, region=Region.BRAZIL)
    )
    second = _RecordingAgent(
        AgentIdentity(
            agent_id="z", agent_type=AgentType.TRADER, region=Region.UNITED_STATES
        )
    )
    with pytest.raises(
        RegionalPriceError, match="Missing regional prices for united_states"
    ):
        run_simulation(
            two_region_state,
            [first, second],
            crusher_configs={},
            months=1,
            regional_prices=regional_prices,
        )
    assert first.calls == second.calls == []


@pytest.mark.parametrize("empty", [False, True])
def test_missing_regional_commodity_coverage_is_rejected(
    state: MarketState,
    regional_prices: tuple[RegionalPrice, ...],
    empty: bool,
) -> None:
    """Require all snapshot commodities when regional prices are explicitly supplied.

    Args:
        state: Initial state.
        regional_prices: Complete Brazilian prices.
        empty: Whether to remove all records rather than one commodity.

    Returns:
        None: Confirms missing prices never fall back to global observations.
    """
    observer = _RecordingAgent(
        AgentIdentity(
            agent_id="observer", agent_type=AgentType.TRADER, region=Region.BRAZIL
        )
    )
    with pytest.raises(RegionalPriceError, match="Missing regional prices"):
        run_simulation(
            state,
            [observer],
            crusher_configs={},
            months=1,
            regional_prices=() if empty else regional_prices[1:],
        )
    assert observer.calls == []


@pytest.mark.parametrize("prices", [42, [{}], ["invalid"]])
def test_invalid_regional_collection_types(state: MarketState, prices: Any) -> None:
    """Require an iterable of RegionalPrice models even for empty registrations.

    Args:
        state: Initial state.
        prices: Malformed regional price input.

    Returns:
        None: Confirms typed regional input validation remains active.
    """
    with pytest.raises(RegionalPriceError):
        run_simulation(state, [], crusher_configs={}, months=1, regional_prices=prices)


@pytest.mark.parametrize("empty", [False, True])
def test_empty_registrations_do_not_require_regional_coverage(
    state: MarketState,
    regional_prices: tuple[RegionalPrice, ...],
    empty: bool,
) -> None:
    """Allow valid partial or empty series when there are no observing agents.

    Args:
        state: Initial state.
        regional_prices: Valid initial series.
        empty: Whether to supply no records instead of one commodity.

    Returns:
        None: Confirms empty simulations retain global behavior and advance dates.
    """
    prices = () if empty else regional_prices[:1]
    assert run_simulation(
        state, [], crusher_configs={}, months=3, regional_prices=prices
    ) == run_simulation(state, [], crusher_configs={}, months=3)
