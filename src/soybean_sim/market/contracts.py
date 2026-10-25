"""Define monthly flow, price, pressure, and matched-trade data without execution."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soybean_sim.models import Commodity, NonEmptyString, NonNegativeTonnes, Region

_PositiveFinite = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]


class MarketFlowType(StrEnum):
    """Distinguish external supply additions from external consumption withdrawals."""

    SUPPLY = "supply"
    CONSUMPTION = "consumption"


class MarketFlow(BaseModel):
    """Describe an external monthly flow in metric tonnes without applying it.

    CONSUMPTION excludes soybean input already handled by crushing settlement.
    Its nonnegative quantity is a withdrawal, rather than a negative supply flow.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    month: Annotated[date, Field(strict=True)]
    region: Region
    commodity: Commodity
    flow_type: MarketFlowType
    quantity_tonnes: NonNegativeTonnes

    @field_validator("month")
    @classmethod
    def month_starts_on_first_day(cls, month: date) -> date:
        """Require a calendar month represented by its first day."""
        if month.day != 1:
            raise ValueError("month must be the first day of a month (YYYY-MM-01)")

        return month


class RegionalPrice(BaseModel):
    """Record a simulated regional USD/tonne price, not a World Bank observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    month: Annotated[date, Field(strict=True)]
    region: Region
    commodity: Commodity
    price_usd_per_tonne: _PositiveFinite

    @field_validator("month")
    @classmethod
    def month_starts_on_first_day(cls, month: date) -> date:
        """Require a calendar month represented by its first day."""
        if month.day != 1:
            raise ValueError("month must be the first day of a month (YYYY-MM-01)")

        return month


class MarketPressure(BaseModel):
    """Record regional supply and demand signals in metric tonnes for pricing.

    These are the market quantities used by the future price dynamics model.
    Their derivation is defined separately; this contract calculates no price.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    month: Annotated[date, Field(strict=True)]
    region: Region
    commodity: Commodity
    supply_tonnes: NonNegativeTonnes
    demand_tonnes: NonNegativeTonnes

    @field_validator("month")
    @classmethod
    def month_starts_on_first_day(cls, month: date) -> date:
        """Require a calendar month represented by its first day."""
        if month.day != 1:
            raise ValueError("month must be the first day of a month (YYYY-MM-01)")

        return month


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
    quantity_tonnes: _PositiveFinite

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
