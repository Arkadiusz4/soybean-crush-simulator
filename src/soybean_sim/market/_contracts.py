"""Define monthly flow, price, pressure, and matched-trade data without execution."""

from datetime import date
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from soybean_sim.models import Commodity, NonNegativeTonnes, Region

from ._pressure import MarketPressure
from ._trade import TradeExecution

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

    @param month: First calendar day of the flow month (``date``).
    @param region: Geographic region of the flow (``Region``).
    @param commodity: Commodity moved by the flow (``Commodity``).
    @param flow_type: Whether the flow is supply or consumption (``MarketFlowType``).
    @param quantity_tonnes: Nonnegative quantity in tonnes (``NonNegativeTonnes``).
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
        """Require a calendar month represented by its first day.

        @param month: Candidate month date to validate (``date``).
        @return: The same date when it falls on the first day (``date``).
        """
        if month.day != 1:
            raise ValueError("month must be the first day of a month (YYYY-MM-01)")

        return month


class RegionalPrice(BaseModel):
    """Record a simulated regional USD/tonne price, not a World Bank observation.

    @param month: First calendar day of the priced month (``date``).
    @param region: Geographic region for the price (``Region``).
    @param commodity: Commodity whose regional price is recorded (``Commodity``).
    @param price_usd_per_tonne: Positive finite USD per tonne price (``float``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    month: Annotated[date, Field(strict=True)]
    region: Region
    commodity: Commodity
    price_usd_per_tonne: _PositiveFinite

    @field_validator("month")
    @classmethod
    def month_starts_on_first_day(cls, month: date) -> date:
        """Require a calendar month represented by its first day.

        @param month: Candidate month date to validate (``date``).
        @return: The same date when it falls on the first day (``date``).
        """
        if month.day != 1:
            raise ValueError("month must be the first day of a month (YYYY-MM-01)")

        return month
