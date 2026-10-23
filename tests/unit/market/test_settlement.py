from collections.abc import Mapping
from datetime import date
from typing import Any

import pytest

from soybean_sim.agents import (
    AgentDecision,
    AgentIdentity,
    AgentType,
    CrusherConfig,
    DecisionAction,
)
from soybean_sim.market import (
    MarketState,
    SettlementError,
    SettlementOutcome,
    SettlementResult,
    models,
    settle_decisions,
    settlement,
)
from soybean_sim.models import Commodity, Inventory, MarketSnapshot, Region


def _quantities(state: MarketState) -> dict[tuple[Region, Commodity], float]:
    """Read physical stock quantities independently of record ordering."""
    return {
        (stock.region, stock.commodity): stock.quantity_tonnes
        for stock in state.snapshot.inventories
    }


def _with_stocks(
    state: MarketState, updates: Mapping[tuple[Region, Commodity], float]
) -> MarketState:
    """Create a validated synthetic state with selected known stock values replaced."""
    return MarketState.from_snapshot(
        MarketSnapshot(
            month=state.snapshot.month,
            prices=state.snapshot.prices,
            inventories=tuple(
                Inventory(
                    region=stock.region,
                    commodity=stock.commodity,
                    quantity_tonnes=updates.get(
                        (stock.region, stock.commodity), stock.quantity_tonnes
                    ),
                )
                for stock in state.snapshot.inventories
            ),
        )
    )


def test_public_exports() -> None:
    """Expose state, result, outcome, function, and domain exception through market."""
    assert MarketState is models.MarketState
    assert SettlementResult is models.SettlementResult
    assert SettlementOutcome is models.SettlementOutcome
    assert SettlementError is settlement.SettlementError
    assert settle_decisions is settlement.settle_decisions


def test_crush_execution_and_mass_conservation(
    state: MarketState, decision: AgentDecision, configs: dict[str, CrusherConfig]
) -> None:
    """Convert soybean input while preserving stocks outside the crusher's region."""
    original = state.model_dump()
    updated, results = settle_decisions(state, [decision], crusher_configs=configs)
    stocks = _quantities(updated)
    assert stocks[(Region.BRAZIL, Commodity.SOYBEANS)] == 4000
    assert stocks[(Region.BRAZIL, Commodity.SOYBEAN_MEAL)] == 4800
    assert stocks[(Region.BRAZIL, Commodity.SOYBEAN_OIL)] == 1200
    assert (
        sum(
            quantity
            for (region, _), quantity in stocks.items()
            if region == Region.BRAZIL
        )
        == 10000
    )
    assert {
        key: value for key, value in stocks.items() if key[0] == Region.UNITED_STATES
    } == {
        key: value
        for key, value in _quantities(state).items()
        if key[0] == Region.UNITED_STATES
    }
    assert all(quantity >= 0 for quantity in stocks.values())
    assert updated.snapshot.month == state.snapshot.month
    assert updated.snapshot.prices == state.snapshot.prices
    assert updated is not state
    assert state.model_dump() == original
    assert len(results) == 1
    assert results[0].decision is decision
    assert results[0].executed_tonnes == 6000
    assert results[0].unfulfilled_tonnes == 0
    assert results[0].outcome is SettlementOutcome.CRUSH_SETTLED
    # Settlement trusts the request rather than recalculating the agent's capacity.
    assert results[0].executed_tonnes > configs["crusher-b"].monthly_capacity_tonnes


def test_insufficient_soybeans_partially_execute(
    state: MarketState, decision: AgentDecision, configs: dict[str, CrusherConfig]
) -> None:
    """Consume available soybeans and record the unfulfilled requested quantity."""
    request = decision.model_copy(update={"quantity_tonnes": 15000})
    updated, results = settle_decisions(state, [request], crusher_configs=configs)
    assert results[0].requested_tonnes == 15000
    assert results[0].executed_tonnes == 10000
    assert results[0].unfulfilled_tonnes == 5000
    stocks = _quantities(updated)
    assert stocks[(Region.BRAZIL, Commodity.SOYBEANS)] == 0
    assert stocks[(Region.BRAZIL, Commodity.SOYBEAN_MEAL)] == 8000
    assert stocks[(Region.BRAZIL, Commodity.SOYBEAN_OIL)] == 2000


@pytest.mark.parametrize("zero_request", [False, True])
def test_zero_request_or_stock(
    state: MarketState,
    decision: AgentDecision,
    configs: dict[str, CrusherConfig],
    zero_request: bool,
) -> None:
    """Execute nothing for zero requested tonnes or known empty soybean stock."""
    source = (
        state
        if zero_request
        else _with_stocks(state, {(Region.BRAZIL, Commodity.SOYBEANS): 0})
    )
    request = (
        decision.model_copy(update={"quantity_tonnes": 0}) if zero_request else decision
    )
    updated, results = settle_decisions(source, [request], crusher_configs=configs)
    assert updated == source
    assert results[0].executed_tonnes == 0
    assert results[0].unfulfilled_tonnes == request.quantity_tonnes


def test_competing_crushers_are_order_independent(
    state: MarketState, decision: AgentDecision, configs: dict[str, CrusherConfig]
) -> None:
    """Allocate first to the lexically smaller ID and use each crusher's own yields."""
    first = decision.model_copy(
        update={
            "agent": AgentIdentity(
                agent_id="crusher-a", agent_type=AgentType.CRUSHER, region=Region.BRAZIL
            ),
            "quantity_tonnes": 5000,
        }
    )
    configs["crusher-a"] = CrusherConfig(
        monthly_capacity_tonnes=1000,
        processing_cost_usd_per_tonne=10,
        meal_yield_fraction=0.5,
        oil_yield_fraction=0.5,
    )
    updated, results = settle_decisions(
        state, [decision, first], crusher_configs=configs
    )
    assert settle_decisions(
        state, iter([first, decision]), crusher_configs=configs
    ) == (updated, results)
    assert [result.decision.agent.agent_id for result in results] == [
        "crusher-a",
        "crusher-b",
    ]
    assert [result.executed_tonnes for result in results] == [5000, 5000]
    assert [result.unfulfilled_tonnes for result in results] == [0, 1000]
    stocks = _quantities(updated)
    assert stocks[(Region.BRAZIL, Commodity.SOYBEANS)] == 0
    assert stocks[(Region.BRAZIL, Commodity.SOYBEAN_MEAL)] == 6500
    assert stocks[(Region.BRAZIL, Commodity.SOYBEAN_OIL)] == 3500


def test_regions_process_independently(
    state: MarketState, decision: AgentDecision, configs: dict[str, CrusherConfig]
) -> None:
    """Consume and produce stocks only in each requesting crusher's region."""
    us_request = decision.model_copy(
        update={
            "agent": AgentIdentity(
                agent_id="crusher-a",
                agent_type=AgentType.CRUSHER,
                region=Region.UNITED_STATES,
            ),
            "quantity_tonnes": 200,
        }
    )
    configs["crusher-a"] = configs["crusher-b"]
    updated, results = settle_decisions(
        state, [decision, us_request], crusher_configs=configs
    )
    assert [result.executed_tonnes for result in results] == [100, 6000]
    stocks = _quantities(updated)
    assert stocks[(Region.UNITED_STATES, Commodity.SOYBEANS)] == 0
    assert stocks[(Region.UNITED_STATES, Commodity.SOYBEAN_MEAL)] == 85
    assert stocks[(Region.UNITED_STATES, Commodity.SOYBEAN_OIL)] == 27


@pytest.mark.parametrize(
    "action", [DecisionAction.PURCHASE, DecisionAction.OFFER_FOR_SALE]
)
def test_trading_is_entirely_deferred(
    state: MarketState, decision: AgentDecision, action: DecisionAction
) -> None:
    """Leave stocks unchanged and report the full trade intention as unfulfilled."""
    intention = decision.model_copy(update={"action": action})
    updated, results = settle_decisions(state, [intention], crusher_configs={})
    assert updated == state
    assert results[0].executed_tonnes == 0
    assert results[0].unfulfilled_tonnes == intention.quantity_tonnes
    assert results[0].outcome is SettlementOutcome.TRADE_MATCHING_NOT_IMPLEMENTED


def test_offers_do_not_reserve_crushing_stock(
    state: MarketState, decision: AgentDecision, configs: dict[str, CrusherConfig]
) -> None:
    """Do not treat a producer's offer as a sale or reservation before processing."""
    offer = decision.model_copy(
        update={
            "agent": AgentIdentity(
                agent_id="a-producer",
                agent_type=AgentType.PRODUCER,
                region=Region.BRAZIL,
            ),
            "action": DecisionAction.OFFER_FOR_SALE,
            "quantity_tonnes": 10000,
        }
    )
    updated, results = settle_decisions(
        state, [decision, offer], crusher_configs=configs
    )
    assert [result.executed_tonnes for result in results] == [0, 6000]
    assert _quantities(updated)[(Region.BRAZIL, Commodity.SOYBEANS)] == 4000


def test_missing_configuration(state: MarketState, decision: AgentDecision) -> None:
    """Identify the crusher whose processing parameters are unavailable."""
    with pytest.raises(
        SettlementError, match=r"Missing processing configuration.*crusher-b"
    ):
        settle_decisions(state, [decision], crusher_configs={})


@pytest.mark.parametrize("meal_yield", [0.7, 0.9, float("nan")])
def test_invalid_processing_yields(
    state: MarketState,
    decision: AgentDecision,
    configs: dict[str, CrusherConfig],
    meal_yield: float,
) -> None:
    """Revalidate copied configurations before consuming soybean stock."""
    configs["crusher-b"] = configs["crusher-b"].model_copy(
        update={"meal_yield_fraction": meal_yield}
    )
    with pytest.raises(
        SettlementError, match=r"crusher-b.*invalid processing configuration"
    ):
        settle_decisions(state, [decision], crusher_configs=configs)


@pytest.mark.parametrize("commodity", list(Commodity))
@pytest.mark.parametrize("quantity", [0, 6000])
def test_missing_crushing_inventory(
    state: MarketState,
    decision: AgentDecision,
    configs: dict[str, CrusherConfig],
    commodity: Commodity,
    quantity: float,
) -> None:
    """Require explicit regional input and output records even for zero processing."""
    incomplete = MarketState.from_snapshot(
        MarketSnapshot(
            month=state.snapshot.month,
            prices=state.snapshot.prices,
            inventories=tuple(
                stock
                for stock in state.snapshot.inventories
                if not (stock.region == Region.BRAZIL and stock.commodity == commodity)
            ),
        )
    )
    request = decision.model_copy(update={"quantity_tonnes": quantity})
    with pytest.raises(
        SettlementError, match=f"missing inventory for brazil/{commodity.value}"
    ):
        settle_decisions(incomplete, [request], crusher_configs=configs)


@pytest.mark.parametrize("agent_type", [AgentType.PRODUCER, AgentType.TRADER])
def test_crush_requires_crusher_identity(
    state: MarketState,
    decision: AgentDecision,
    configs: dict[str, CrusherConfig],
    agent_type: AgentType,
) -> None:
    """Reject physical processing attributed to another agent role."""
    request = decision.model_copy(
        update={"agent": decision.agent.model_copy(update={"agent_type": agent_type})}
    )
    with pytest.raises(SettlementError, match="must be a crusher"):
        settle_decisions(state, [request], crusher_configs=configs)


def test_decision_month_must_match(
    state: MarketState, decision: AgentDecision, configs: dict[str, CrusherConfig]
) -> None:
    """Reject otherwise valid decisions for another calendar month."""
    request = decision.model_copy(update={"month": date(2021, 2, 1)})
    with pytest.raises(SettlementError, match="does not match market month"):
        settle_decisions(state, [request], crusher_configs=configs)


@pytest.mark.parametrize(
    "changes",
    [
        {"commodity": Commodity.SOYBEAN_OIL},
        {"quantity_tonnes": -1},
        {"quantity_tonnes": float("inf")},
        {"quantity_tonnes": float("nan")},
    ],
)
def test_invalid_decisions_are_not_silently_ignored(
    state: MarketState,
    decision: AgentDecision,
    configs: dict[str, CrusherConfig],
    changes: dict[str, object],
) -> None:
    """Report invalid copied decisions through the settlement domain error."""
    request = decision.model_copy(update=changes)
    with pytest.raises(SettlementError, match="Invalid decision"):
        settle_decisions(state, [request], crusher_configs=configs)


@pytest.mark.parametrize("quantity", [6000, 1000])
def test_duplicate_crush_requests_are_rejected(
    state: MarketState,
    decision: AgentDecision,
    configs: dict[str, CrusherConfig],
    quantity: float,
) -> None:
    """Reject repeated crusher IDs, including identical request submissions."""
    duplicate = decision.model_copy(update={"quantity_tonnes": quantity})
    with pytest.raises(SettlementError, match=r"Duplicate CRUSH request.*crusher-b"):
        settle_decisions(state, [decision, duplicate], crusher_configs=configs)


@pytest.mark.parametrize(
    "changes", [{"region": Region.UNITED_STATES}, {"agent_type": AgentType.TRADER}]
)
def test_conflicting_identities_are_rejected(
    state: MarketState,
    decision: AgentDecision,
    configs: dict[str, CrusherConfig],
    changes: dict[str, object],
) -> None:
    """Require one consistent identity for each agent ID across a settlement batch."""
    conflict = decision.model_copy(
        update={
            "agent": decision.agent.model_copy(update=changes),
            "action": DecisionAction.PURCHASE,
        }
    )
    with pytest.raises(SettlementError, match=r"Conflicting identities.*crusher-b"):
        settle_decisions(state, [decision, conflict], crusher_configs=configs)


def test_deferred_trades_still_require_known_regional_records(
    state: MarketState, decision: AgentDecision
) -> None:
    """Reject a reference to an unrepresented region even when trading is deferred."""
    request = decision.model_copy(
        update={
            "agent": decision.agent.model_copy(update={"region": Region.ARGENTINA}),
            "action": DecisionAction.PURCHASE,
        }
    )
    with pytest.raises(SettlementError, match="region argentina is not represented"):
        settle_decisions(state, [request], crusher_configs={})


def test_failed_batch_leaves_original_state_unchanged(
    state: MarketState, decision: AgentDecision, configs: dict[str, CrusherConfig]
) -> None:
    """Discard local processing if a later crusher's output would overflow."""
    source = _with_stocks(
        state,
        {
            (Region.BRAZIL, Commodity.SOYBEANS): 1.7e308,
            (Region.BRAZIL, Commodity.SOYBEAN_MEAL): 1.7e308,
        },
    )
    first = decision.model_copy(
        update={
            "agent": AgentIdentity(
                agent_id="crusher-a",
                agent_type=AgentType.CRUSHER,
                region=Region.UNITED_STATES,
            ),
            "quantity_tonnes": 100,
        }
    )
    second = decision.model_copy(update={"quantity_tonnes": 1.7e308})
    configs["crusher-a"] = configs["crusher-b"]
    original = source.model_dump()
    with pytest.raises(SettlementError, match="resulting inventories must be finite"):
        settle_decisions(source, [second, first], crusher_configs=configs)
    assert source.model_dump() == original


def test_empty_batch(state: MarketState) -> None:
    """Preserve physical state and return no results when nothing is submitted."""
    updated, results = settle_decisions(state, iter(()), crusher_configs={})
    assert updated == state
    assert results == ()


def test_wrong_configuration_type(state: MarketState, decision: AgentDecision) -> None:
    """Reject parameter dictionaries at the typed configuration boundary."""
    configs: dict[str, Any] = {"crusher-b": {"meal_yield_fraction": 0.8}}
    with pytest.raises(SettlementError, match="expected a CrusherConfig"):
        settle_decisions(state, [decision], crusher_configs=configs)


def test_purchase_without_regional_soybean_stock(
    state: MarketState, decision: AgentDecision
) -> None:
    """Defer demand without requiring soybean ownership or a stock record."""
    source = MarketState.from_snapshot(
        MarketSnapshot(
            month=state.snapshot.month,
            prices=state.snapshot.prices,
            inventories=tuple(
                stock
                for stock in state.snapshot.inventories
                if not (
                    stock.region == Region.BRAZIL
                    and stock.commodity == Commodity.SOYBEANS
                )
            ),
        )
    )
    intention = decision.model_copy(update={"action": DecisionAction.PURCHASE})
    updated, results = settle_decisions(source, [intention], crusher_configs={})
    assert updated == source
    assert results[0].outcome is SettlementOutcome.TRADE_MATCHING_NOT_IMPLEMENTED
    assert results[0].unfulfilled_tonnes == intention.quantity_tonnes


def test_unrepresented_trade_commodity(
    state: MarketState, decision: AgentDecision
) -> None:
    """Reject commodities absent from both reference prices and regional stocks."""
    source = MarketState.from_snapshot(
        MarketSnapshot(
            month=state.snapshot.month,
            prices=tuple(
                price
                for price in state.snapshot.prices
                if price.commodity != Commodity.SOYBEAN_OIL
            ),
            inventories=tuple(
                stock
                for stock in state.snapshot.inventories
                if stock.commodity != Commodity.SOYBEAN_OIL
            ),
        )
    )
    intention = decision.model_copy(
        update={
            "action": DecisionAction.OFFER_FOR_SALE,
            "commodity": Commodity.SOYBEAN_OIL,
        }
    )
    with pytest.raises(
        SettlementError, match="commodity soybean_oil is not represented"
    ):
        settle_decisions(source, [intention], crusher_configs={})


def test_fractional_stock_never_becomes_negative(
    state: MarketState, decision: AgentDecision, configs: dict[str, CrusherConfig]
) -> None:
    """Keep fractional stock exhaustion nonnegative despite rounding residuals."""
    source = _with_stocks(state, {(Region.BRAZIL, Commodity.SOYBEANS): 0.3})
    first = decision.model_copy(
        update={
            "agent": decision.agent.model_copy(update={"agent_id": "crusher-a"}),
            "quantity_tonnes": 0.1,
        }
    )
    second = decision.model_copy(update={"quantity_tonnes": 0.2})
    configs["crusher-a"] = configs["crusher-b"]
    updated, results = settle_decisions(
        source, [second, first], crusher_configs=configs
    )
    stocks = _quantities(updated)
    assert stocks[(Region.BRAZIL, Commodity.SOYBEANS)] == 0
    assert all(quantity >= 0 for quantity in stocks.values())
    assert sum(result.executed_tonnes for result in results) == pytest.approx(0.3)
    assert sum(
        quantity for (region, _), quantity in stocks.items() if region == Region.BRAZIL
    ) == pytest.approx(0.3)
    assert all(result.executed_tonnes <= result.requested_tonnes for result in results)


def test_trader_can_submit_several_intentions(
    state: MarketState, decision: AgentDecision
) -> None:
    """Allow a consistent trader identity on multiple distinct deferred intentions."""
    identity = AgentIdentity(
        agent_id="trader-1", agent_type=AgentType.TRADER, region=Region.BRAZIL
    )
    purchase = decision.model_copy(
        update={"agent": identity, "action": DecisionAction.PURCHASE}
    )
    sale = decision.model_copy(
        update={
            "agent": identity,
            "action": DecisionAction.OFFER_FOR_SALE,
            "commodity": Commodity.SOYBEAN_MEAL,
        }
    )
    updated, results = settle_decisions(state, [purchase, sale], crusher_configs={})
    assert updated == state
    assert [result.decision for result in results] == [sale, purchase]
    assert all(result.executed_tonnes == 0 for result in results)
