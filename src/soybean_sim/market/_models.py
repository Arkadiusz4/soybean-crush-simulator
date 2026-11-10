"""Represent immutable market observations and physical decision settlement results."""

import math
from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, model_validator

from soybean_sim.agents import AgentDecision, DecisionAction
from soybean_sim.models import MarketSnapshot, NonNegativeTonnes

_QUANTITY_REL_TOLERANCE: Final = 1e-12
_QUANTITY_ABS_TOLERANCE_TONNES: Final = 1e-9


class MarketState(BaseModel):
    """Hold one frozen snapshot as the current month, prices, and regional stocks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot: MarketSnapshot

    @classmethod
    def from_snapshot(cls, snapshot: MarketSnapshot) -> Self:
        """Validate initial observations and their nested prices and stocks."""
        validated = MarketSnapshot.model_validate(snapshot.model_dump())
        return cls(snapshot=validated)


class SettlementOutcome(StrEnum):
    """Distinguish evaluated physical crushing from unimplemented trade matching."""

    CRUSH_SETTLED = "crush_settled"
    TRADE_MATCHING_NOT_IMPLEMENTED = "trade_matching_not_implemented"


class SettlementResult(BaseModel):
    """Record requested, executed, and unfulfilled input tonnes for one decision.

    Crush settlement may execute partly or not at all. Trading is always deferred.
    Quantity accounting uses relative tolerance 1e-12 and absolute tolerance 1e-9 t.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: AgentDecision
    requested_tonnes: NonNegativeTonnes
    executed_tonnes: NonNegativeTonnes
    unfulfilled_tonnes: NonNegativeTonnes
    outcome: SettlementOutcome

    @model_validator(mode="after")
    def quantities_account_for_request(self) -> Self:
        """Preserve the request and prevent overexecution or lost quantities."""
        if self.requested_tonnes != self.decision.quantity_tonnes:
            raise ValueError(
                "requested_tonnes must equal the original decision quantity"
            )

        if self.executed_tonnes > self.requested_tonnes:
            raise ValueError("executed_tonnes cannot exceed requested_tonnes")

        if not math.isclose(
            self.requested_tonnes,
            self.executed_tonnes + self.unfulfilled_tonnes,
            rel_tol=_QUANTITY_REL_TOLERANCE,
            abs_tol=_QUANTITY_ABS_TOLERANCE_TONNES,
        ):
            raise ValueError(
                "requested_tonnes must equal executed plus unfulfilled tonnes"
            )

        return self

    @model_validator(mode="after")
    def outcome_matches_action(self) -> Self:
        """Prevent trading intentions from being reported as executed trades."""
        if self.decision.action == DecisionAction.CRUSH:
            if self.outcome != SettlementOutcome.CRUSH_SETTLED:
                raise ValueError("CRUSH results must have outcome crush_settled")
        elif (
            self.outcome != SettlementOutcome.TRADE_MATCHING_NOT_IMPLEMENTED
            or self.executed_tonnes != 0
            or self.unfulfilled_tonnes != self.requested_tonnes
        ):
            raise ValueError("trading intentions must remain entirely unexecuted")

        return self
