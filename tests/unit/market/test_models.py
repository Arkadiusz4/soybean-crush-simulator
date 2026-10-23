from datetime import date

import pytest
from pydantic import ValidationError

from soybean_sim.agents import AgentDecision, DecisionAction
from soybean_sim.market import MarketState, SettlementOutcome, SettlementResult
from soybean_sim.models import Commodity, MarketSnapshot


def test_initial_state_reuses_snapshot_contract(state: MarketState) -> None:
    """Expose only monthly observations, preserving validated quantities and prices."""
    initial = MarketState.from_snapshot(state.snapshot)
    assert initial.snapshot == state.snapshot
    assert isinstance(initial.snapshot, MarketSnapshot)
    assert initial.snapshot.month == date(2021, 1, 1)
    assert len(initial.snapshot.inventories) == 6
    assert {item.commodity for item in initial.snapshot.prices} == set(Commodity)


def test_state_is_frozen_and_serializable(state: MarketState) -> None:
    """Keep both state and its nested records immutable, with no annual flow fields."""
    assert MarketState.model_validate_json(state.model_dump_json()) == state
    with pytest.raises(ValidationError, match="frozen_instance"):
        state.snapshot = state.snapshot
    with pytest.raises(ValidationError, match="frozen_instance"):
        state.snapshot.inventories[0].quantity_tonnes = 10
    with pytest.raises(ValidationError, match="extra_forbidden"):
        MarketState.model_validate({**state.model_dump(), "balances": []})


@pytest.mark.parametrize("defect", ["month", "prices", "inventories", "quantity"])
def test_from_snapshot_revalidates_nested_observations(
    state: MarketState, defect: str
) -> None:
    """Revalidate copied snapshots, including their nested inventory quantities."""
    snapshot = state.snapshot
    changes = {
        "month": {"month": date(2021, 1, 2)},
        "prices": {"prices": ()},
        "inventories": {
            "inventories": (*snapshot.inventories, snapshot.inventories[0])
        },
        "quantity": {
            "inventories": (
                snapshot.inventories[0].model_copy(update={"quantity_tonnes": -1}),
            )
        },
    }
    with pytest.raises(ValidationError):
        MarketState.from_snapshot(snapshot.model_copy(update=changes[defect]))


@pytest.mark.parametrize(
    ("executed", "unfulfilled"), [(0, 6000), (1000, 5000), (6000, 0)]
)
def test_valid_crush_result(
    decision: AgentDecision, executed: float, unfulfilled: float
) -> None:
    """Describe partial, full, or zero execution of the original intention."""
    result = SettlementResult(
        decision=decision,
        requested_tonnes=6000,
        executed_tonnes=executed,
        unfulfilled_tonnes=unfulfilled,
        outcome=SettlementOutcome.CRUSH_SETTLED,
    )
    assert result.decision is decision
    assert result.requested_tonnes == result.executed_tonnes + result.unfulfilled_tonnes
    assert SettlementResult.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError, match="frozen_instance"):
        result.executed_tonnes = 1


@pytest.mark.parametrize(
    "field", ["requested_tonnes", "executed_tonnes", "unfulfilled_tonnes"]
)
@pytest.mark.parametrize("quantity", [-1, float("nan"), float("inf")])
def test_invalid_result_quantities(
    decision: AgentDecision, field: str, quantity: float
) -> None:
    """Reject negative and nonfinite accounting quantities."""
    payload = {
        "decision": decision,
        "requested_tonnes": 6000,
        "executed_tonnes": 0,
        "unfulfilled_tonnes": 6000,
        "outcome": SettlementOutcome.CRUSH_SETTLED,
    }
    with pytest.raises(ValidationError, match=field):
        SettlementResult.model_validate({**payload, field: quantity})


@pytest.mark.parametrize(
    ("requested", "executed", "unfulfilled", "message"),
    [
        (5999, 0, 5999, "original decision quantity"),
        (6000, 6001, 0, "cannot exceed requested"),
        (6000, 6000.0000000001, 0, "cannot exceed requested"),
        (6000, 100, 500, "executed plus unfulfilled"),
    ],
)
def test_result_accounting_invariants(
    decision: AgentDecision,
    requested: float,
    executed: float,
    unfulfilled: float,
    message: str,
) -> None:
    """Enforce original request equality and prevent overexecution despite tolerance."""
    with pytest.raises(ValidationError, match=message):
        SettlementResult(
            decision=decision,
            requested_tonnes=requested,
            executed_tonnes=executed,
            unfulfilled_tonnes=unfulfilled,
            outcome=SettlementOutcome.CRUSH_SETTLED,
        )


@pytest.mark.parametrize(("difference", "valid"), [(1e-9, True), (0.01, False)])
def test_result_quantity_tolerance(
    decision: AgentDecision, difference: float, valid: bool
) -> None:
    """Accept rounding-scale residuals while rejecting material accounting errors."""
    payload = {
        "decision": decision,
        "requested_tonnes": 6000,
        "executed_tonnes": 1,
        "unfulfilled_tonnes": 5999 + difference,
        "outcome": SettlementOutcome.CRUSH_SETTLED,
    }
    if valid:
        assert SettlementResult.model_validate(payload).executed_tonnes == 1
    else:
        with pytest.raises(ValidationError, match="executed plus unfulfilled"):
            SettlementResult.model_validate(payload)


@pytest.mark.parametrize(
    "action", [DecisionAction.PURCHASE, DecisionAction.OFFER_FOR_SALE]
)
@pytest.mark.parametrize("defect", ["execution", "outcome", "unfulfilled"])
def test_trades_cannot_be_reported_as_executed(
    decision: AgentDecision, action: DecisionAction, defect: str
) -> None:
    """Require deferred outcomes, zero execution, and exact unfulfilled requests."""
    intention = decision.model_copy(update={"action": action})
    changes = {
        "execution": {"executed_tonnes": 10, "unfulfilled_tonnes": 5990},
        "outcome": {"outcome": SettlementOutcome.CRUSH_SETTLED},
        "unfulfilled": {"unfulfilled_tonnes": 6000.000000001},
    }
    with pytest.raises(ValidationError, match="entirely unexecuted"):
        SettlementResult.model_validate(
            {
                "decision": intention,
                "requested_tonnes": 6000,
                "executed_tonnes": 0,
                "unfulfilled_tonnes": 6000,
                "outcome": SettlementOutcome.TRADE_MATCHING_NOT_IMPLEMENTED,
                **changes[defect],
            }
        )


def test_crush_outcome_must_match_action(decision: AgentDecision) -> None:
    """Keep physical settlement distinct from deferred trade matching."""
    with pytest.raises(
        ValidationError, match="CRUSH results must have outcome crush_settled"
    ):
        SettlementResult(
            decision=decision,
            requested_tonnes=6000,
            executed_tonnes=0,
            unfulfilled_tonnes=6000,
            outcome=SettlementOutcome.TRADE_MATCHING_NOT_IMPLEMENTED,
        )
