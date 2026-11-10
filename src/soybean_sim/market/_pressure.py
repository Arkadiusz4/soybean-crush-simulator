"""Define regional monthly supply and demand signals for future price dynamics."""

from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from soybean_sim.models import Commodity, NonNegativeTonnes, Region


class MarketPressure(BaseModel):
    """Record regional supply and demand signals in metric tonnes for pricing.

    These are the market quantities used by the future price dynamics model.
    Their derivation is defined separately; this contract calculates no price.

    @param month: First calendar day of the pressure month (``date``).
    @param region: Geographic region for the signals (``Region``).
    @param commodity: Commodity whose supply and demand are recorded (``Commodity``).
    @param supply_tonnes: Supply signal in tonnes (``NonNegativeTonnes``).
    @param demand_tonnes: Demand signal in tonnes (``NonNegativeTonnes``).
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
        """Require a calendar month represented by its first day.

        @param month: Candidate month date to validate (``date``).
        @return: The same date when it falls on the first day (``date``).
        """
        if month.day != 1:
            raise ValueError("month must be the first day of a month (YYYY-MM-01)")

        return month
