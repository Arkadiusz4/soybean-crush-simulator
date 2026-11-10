"""Validate annual balances, monthly USD prices, and initial inventories in tonnes."""

from datetime import date
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from ._enums import Commodity, Region

NonNegativeTonnes = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
NonEmptyString = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]


class MarketBalance(BaseModel):
    """Record one region's marketing-year balance in nonnegative metric tonnes.

    None means unobserved; only crush may be omitted. Preserve the source year label
    without allocating flows to months or enforcing exact accounting balance.

    Args:
        region (Region): Geographic region for the balance.
        commodity (Commodity): Commodity covered by the balance.
        marketing_year (NonEmptyString): Source marketing-year label.
        production_tonnes (NonNegativeTonnes | None): Annual production in tonnes, or
            None if unobserved.
        imports_tonnes (NonNegativeTonnes | None): Annual imports in tonnes, or None if
            unobserved.
        exports_tonnes (NonNegativeTonnes | None): Annual exports in tonnes, or None if
            unobserved.
        domestic_consumption_tonnes (NonNegativeTonnes | None): Annual domestic
            consumption in tonnes, or None if unobserved.
        beginning_stocks_tonnes (NonNegativeTonnes | None): Beginning stocks in tonnes,
            or None if unobserved.
        ending_stocks_tonnes (NonNegativeTonnes | None): Ending stocks in tonnes, or
            None if unobserved.
        crush_tonnes (NonNegativeTonnes | None): Soybean crush input in tonnes, or None
            if omitted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    region: Region
    commodity: Commodity
    marketing_year: NonEmptyString
    production_tonnes: NonNegativeTonnes | None
    imports_tonnes: NonNegativeTonnes | None
    exports_tonnes: NonNegativeTonnes | None
    domestic_consumption_tonnes: NonNegativeTonnes | None
    beginning_stocks_tonnes: NonNegativeTonnes | None
    ending_stocks_tonnes: NonNegativeTonnes | None
    crush_tonnes: NonNegativeTonnes | None = None

    @model_validator(mode="after")
    def crush_applies_to_soybeans(self) -> Self:
        """Check that crush measures soybean input and appears only on soybeans.

        Returns:
            Self: The validated balance instance.
        """
        if self.crush_tonnes is not None and self.commodity != Commodity.SOYBEANS:
            raise ValueError("crush_tonnes applies only to soybeans")
        return self


class CommodityPrice(BaseModel):
    """Record a positive, finite monthly reference price in USD per metric tonne.

    The month is its first calendar day. The source label is required; prices are
    not region-specific, and records are immutable.

    Args:
        commodity (Commodity): Commodity whose reference price is recorded.
        month (date): First calendar day of the priced month.
        price_usd_per_tonne (float): Positive finite USD per tonne price.
        data_source (NonEmptyString): Required source label for the price.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    commodity: Commodity
    month: Annotated[date, Field(strict=True)]
    price_usd_per_tonne: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    data_source: NonEmptyString

    @field_validator("month")
    @classmethod
    def month_starts_on_first_day(cls, month: date) -> date:
        """Check that the date represents the first day of its month.

        Args:
            month (date): Candidate month date to validate.

        Returns:
            date: The same date when it falls on the first day.
        """
        if month.day != 1:
            raise ValueError("month must be the first day of a month (YYYY-MM-01)")
        return month


class Inventory(BaseModel):
    """Record immutable initial stock for one region and commodity in metric tonnes.

    Quantity must be finite and nonnegative; zero is known empty stock, not missing.

    Args:
        region (Region): Geographic region holding the stock.
        commodity (Commodity): Commodity held in inventory.
        quantity_tonnes (NonNegativeTonnes): Finite nonnegative stock in tonnes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    region: Region
    commodity: Commodity
    quantity_tonnes: NonNegativeTonnes
