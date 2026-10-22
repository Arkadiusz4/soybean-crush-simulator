from datetime import date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from soybean_sim.agents import AgentDecision, AgentIdentity, AgentType, DecisionAction
from soybean_sim.agents import models as implementation
from soybean_sim.models import Commodity, Region


@pytest.fixture
def identity() -> AgentIdentity:
    """Provide a caller-assigned identity for a hypothetical Brazilian crusher."""
    return AgentIdentity(
        agent_id="crusher-br-1", agent_type=AgentType.CRUSHER, region=Region.BRAZIL
    )


@pytest.fixture
def decision(identity: AgentIdentity) -> AgentDecision:
    """Provide a synthetic request to crush ten tonnes of soybean input."""
    return AgentDecision(
        agent=identity,
        month=date(2021, 1, 1),
        action=DecisionAction.CRUSH,
        commodity=Commodity.SOYBEANS,
        quantity_tonnes=10,
    )


def test_public_exports() -> None:
    """Expose the stable identity, role, decision, and action types."""
    assert AgentIdentity is implementation.AgentIdentity
    assert AgentType is implementation.AgentType
    assert AgentDecision is implementation.AgentDecision
    assert DecisionAction is implementation.DecisionAction


@pytest.mark.parametrize("agent_type", list(AgentType))
def test_valid_identity(agent_type: AgentType) -> None:
    """Accept supported roles and normalize caller-assigned nonblank identifiers."""
    identity = AgentIdentity(
        agent_id=" agent-1 ", agent_type=agent_type, region=Region.BRAZIL
    )
    assert identity.agent_id == "agent-1"
    assert identity.agent_type is agent_type
    assert identity.region is Region.BRAZIL


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("agent_id", ""),
        ("agent_id", "  "),
        ("agent_id", 1),
        ("agent_id", None),
        ("agent_type", "unknown"),
        ("region", "unknown"),
    ],
)
def test_invalid_identity(identity: AgentIdentity, field: str, value: Any) -> None:
    """Reject blank identifiers and unsupported roles or regions."""
    with pytest.raises(ValidationError, match=field):
        AgentIdentity.model_validate({**identity.model_dump(), field: value})


@pytest.mark.parametrize("field", ["agent_id", "agent_type", "region"])
def test_identity_fields_are_required(identity: AgentIdentity, field: str) -> None:
    """Require callers to provide the agent's identifier, role, and region."""
    payload = identity.model_dump()
    del payload[field]
    with pytest.raises(ValidationError, match=field):
        AgentIdentity.model_validate(payload)


@pytest.mark.parametrize(
    ("action", "commodity"),
    [
        (DecisionAction.OFFER_FOR_SALE, Commodity.SOYBEANS),
        (DecisionAction.OFFER_FOR_SALE, Commodity.SOYBEAN_MEAL),
        (DecisionAction.OFFER_FOR_SALE, Commodity.SOYBEAN_OIL),
        (DecisionAction.PURCHASE, Commodity.SOYBEANS),
        (DecisionAction.CRUSH, Commodity.SOYBEANS),
    ],
)
def test_valid_decisions(
    decision: AgentDecision, action: DecisionAction, commodity: Commodity
) -> None:
    """Represent sale, purchase, and crush intentions with typed actions."""
    proposed = AgentDecision.model_validate(
        {**decision.model_dump(), "action": action, "commodity": commodity}
    )
    assert proposed.action is action
    assert proposed.commodity is commodity
    assert proposed.quantity_tonnes == 10


def test_zero_quantity_is_no_operation(decision: AgentDecision) -> None:
    """Preserve a zero request without turning it into an executed operation."""
    proposed = AgentDecision.model_validate(
        {**decision.model_dump(), "quantity_tonnes": 0}
    )
    assert proposed.quantity_tonnes == 0


@pytest.mark.parametrize(
    "quantity", [-1, float("nan"), float("inf"), float("-inf"), None, True, "10"]
)
def test_invalid_quantities(decision: AgentDecision, quantity: Any) -> None:
    """Reuse finite nonnegative tonne validation, rejecting implicit coercion."""
    with pytest.raises(ValidationError, match="quantity_tonnes"):
        AgentDecision.model_validate(
            {**decision.model_dump(), "quantity_tonnes": quantity}
        )


@pytest.mark.parametrize(
    "month", [date(2021, 1, 2), datetime(2021, 1, 1), "2021-01-01", None]
)
def test_invalid_decision_months(decision: AgentDecision, month: Any) -> None:
    """Require decisions to use explicit first-of-month dates like snapshots."""
    with pytest.raises(ValidationError, match="month"):
        AgentDecision.model_validate({**decision.model_dump(), "month": month})


@pytest.mark.parametrize("commodity", [Commodity.SOYBEAN_MEAL, Commodity.SOYBEAN_OIL])
def test_crush_uses_soybean_input(
    decision: AgentDecision, commodity: Commodity
) -> None:
    """Keep the requested crush quantity separate from meal and oil output."""
    with pytest.raises(ValidationError, match="crush decisions apply only to soybeans"):
        AgentDecision.model_validate({**decision.model_dump(), "commodity": commodity})


@pytest.mark.parametrize(
    ("field", "value"), [("action", "unknown"), ("commodity", "unknown")]
)
def test_unknown_decision_identifiers(
    decision: AgentDecision, field: str, value: str
) -> None:
    """Reject free-form actions and unsupported commodities."""
    with pytest.raises(ValidationError, match=field):
        AgentDecision.model_validate({**decision.model_dump(), field: value})


@pytest.mark.parametrize(
    "field", ["agent", "month", "action", "commodity", "quantity_tonnes"]
)
def test_decision_fields_are_required(decision: AgentDecision, field: str) -> None:
    """Require explicit attribution, timing, action, commodity, and requested tonnes."""
    payload = decision.model_dump()
    del payload[field]
    with pytest.raises(ValidationError, match=field):
        AgentDecision.model_validate(payload)


def test_frozen_models(identity: AgentIdentity, decision: AgentDecision) -> None:
    """Keep both the decision and its nested identity immutable."""
    with pytest.raises(ValidationError, match="frozen_instance"):
        identity.agent_id = "changed"
    with pytest.raises(ValidationError, match="frozen_instance"):
        decision.quantity_tonnes = 20
    with pytest.raises(ValidationError, match="frozen_instance"):
        decision.agent.region = Region.ARGENTINA


def test_round_trips_and_extra_fields(
    identity: AgentIdentity, decision: AgentDecision
) -> None:
    """Serialize typed intentions and reject undeclared execution fields."""
    assert AgentIdentity.model_validate_json(identity.model_dump_json()) == identity
    assert AgentDecision.model_validate_json(decision.model_dump_json()) == decision
    serialized = decision.model_dump(mode="json")
    assert serialized["action"] == "crush"
    assert serialized["agent"]["agent_type"] == "crusher"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AgentIdentity.model_validate({**identity.model_dump(), "registry": {}})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AgentDecision.model_validate({**decision.model_dump(), "execution_price": 400})
