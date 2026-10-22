"""Define agent identities and proposed actions without executing market changes."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soybean_sim.models import Commodity, NonEmptyString, NonNegativeTonnes, Region


class AgentType(StrEnum):
    """Identify the three agent roles without prescribing their decision rules."""

    PRODUCER = "producer"
    CRUSHER = "crusher"
    TRADER = "trader"


class DecisionAction(StrEnum):
    """Identify sale offers, purchase requests, and soybean processing requests."""

    OFFER_FOR_SALE = "offer_for_sale"
    PURCHASE = "purchase"
    CRUSH = "crush"


class AgentIdentity(BaseModel):
    """Identify an agent by a caller-assigned unique ID, role, and home region."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: NonEmptyString
    agent_type: AgentType
    region: Region


class AgentDecision(BaseModel):
    """Record an agent's intention for one month without changing market state.

    Quantity is requested metric tonnes; zero requests no operation. Crush
    quantity measures soybean input, not meal or oil output.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent: AgentIdentity
    month: Annotated[date, Field(strict=True)]
    action: DecisionAction
    commodity: Commodity
    quantity_tonnes: NonNegativeTonnes

    @field_validator("month")
    @classmethod
    def month_starts_on_first_day(cls, month: date) -> date:
        """Require a normalized monthly date without inferring a reporting period."""
        if month.day != 1:
            raise ValueError("month must be the first day of a month (YYYY-MM-01)")
        return month

    @model_validator(mode="after")
    def crush_requests_soybean_input(self) -> Self:
        """Keep crushing requests consistent with the existing soybean-input model."""
        if self.action == DecisionAction.CRUSH and self.commodity != Commodity.SOYBEANS:
            raise ValueError("crush decisions apply only to soybeans")
        return self
