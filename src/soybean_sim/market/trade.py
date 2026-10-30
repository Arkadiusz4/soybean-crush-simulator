"""Define matched interregional physical trades without applying inventory changes."""

from datetime import date
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soybean_sim.models import Commodity, NonEmptyString, Region


class TradeExecution(BaseModel):
    """Record a matched physical interregional trade without changing inventories.

    Quantity is executed metric tonnes, rather than an unfulfilled intention.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    month: Annotated[date, Field(strict=True)]
    seller_agent_id: NonEmptyString
    buyer_agent_id: NonEmptyString
    source_region: Region
    destination_region: Region
    commodity: Commodity
    quantity_tonnes: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]

    @field_validator("month")
    @classmethod
    def month_starts_on_first_day(cls, month: date) -> date:
        """Require a calendar month represented by its first day."""
        if month.day != 1:
            raise ValueError("month must be the first day of a month (YYYY-MM-01)")

        return month

    @model_validator(mode="after")
    def participants_and_regions_differ(self) -> Self:
        """Require distinct participants and an actual interregional route."""
        if self.seller_agent_id == self.buyer_agent_id:
            raise ValueError("seller_agent_id and buyer_agent_id must differ")

        if self.source_region == self.destination_region:
            raise ValueError("source_region and destination_region must differ")

        return self
