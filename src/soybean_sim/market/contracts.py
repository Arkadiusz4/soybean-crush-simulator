"""Define monthly flow, price, pressure, and matched-trade data without execution."""

from datetime import date
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from soybean_sim.models import Commodity, NonNegativeTonnes, Region

from .pressure import MarketPressure
from .trade import TradeExecution

__all__ = [
    "MarketFlow",
    "MarketFlowType",
    "MarketPressure",
    "RegionalPrice",
    "TradeExecution",
]

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
