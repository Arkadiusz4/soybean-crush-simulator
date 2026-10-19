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

from soybean_sim.models.enums import Commodity, Region

NonNegativeTonnes = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
NonEmptyString = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]


class MarketBalance(BaseModel):
    """Reported marketing-year balance in metric tons; None means unobserved."""

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
        """Crush is soybean input volume, not meal or oil output volume."""
        if self.crush_tonnes is not None and self.commodity != Commodity.SOYBEANS:
            raise ValueError("crush_tonnes applies only to soybeans")
        return self


class CommodityPrice(BaseModel):
    """Monthly reference price; month is represented by its first calendar day."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    commodity: Commodity
    month: Annotated[date, Field(strict=True)]
    price_usd_per_tonne: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    data_source: NonEmptyString

    @field_validator("month")
    @classmethod
    def month_starts_on_first_day(cls, month: date) -> date:
        """Require an explicit monthly period instead of silently rounding dates."""
        if month.day != 1:
            raise ValueError("month must be the first day of a month (YYYY-MM-01)")
        return month


class Inventory(BaseModel):
    """Known stock quantity for one region and commodity at initialization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    region: Region
    commodity: Commodity
    quantity_tonnes: NonNegativeTonnes
