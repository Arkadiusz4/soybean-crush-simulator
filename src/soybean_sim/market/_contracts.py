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

    Args:
        month (date): First calendar day of the flow month.
        region (Region): Geographic region of the flow.
        commodity (Commodity): Commodity moved by the flow.
        flow_type (MarketFlowType): Whether the flow is supply or consumption.
        quantity_tonnes (NonNegativeTonnes): Nonnegative quantity in tonnes.
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

        Args:
            month (date): Candidate month date to validate.

        Returns:
            date: The same date when it falls on the first day.
        """
        if month.day != 1:
            raise ValueError("month must be the first day of a month (YYYY-MM-01)")

        return month


class RegionalPrice(BaseModel):
    """Record a simulated regional USD/tonne price, not a World Bank observation.

    Args:
        month (date): First calendar day of the priced month.
        region (Region): Geographic region for the price.
        commodity (Commodity): Commodity whose regional price is recorded.
        price_usd_per_tonne (float): Positive finite USD per tonne price.
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

        Args:
            month (date): Candidate month date to validate.

        Returns:
            date: The same date when it falls on the first day.
        """
        if month.day != 1:
            raise ValueError("month must be the first day of a month (YYYY-MM-01)")

        return month
