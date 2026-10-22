"""Describe immutable monthly observations available to agents before decisions."""

from datetime import date
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .market import CommodityPrice, Inventory


class MarketSnapshot(BaseModel):
    """Expose global reference prices and regional stocks for a calendar month.

    Prices are not regional transaction prices. Missing inventory entries do not
    imply zero stock; annual balances and monthly flow estimates are excluded.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    month: Annotated[date, Field(strict=True)]
    prices: Annotated[tuple[CommodityPrice, ...], Field(min_length=1)]
    inventories: tuple[Inventory, ...]

    @field_validator("month")
    @classmethod
    def month_starts_on_first_day(cls, month: date) -> date:
        """Require the same first-of-month date representation as commodity prices."""
        if month.day != 1:
            raise ValueError("month must be the first day of a month (YYYY-MM-01)")
        return month

    @model_validator(mode="after")
    def observations_are_unambiguous(self) -> Self:
        """Check price uniqueness and timing, and regional inventory uniqueness."""
        commodities = [price.commodity for price in self.prices]
        if len(commodities) != len(set(commodities)):
            raise ValueError("prices must be unique by commodity")

        if any(price.month != self.month for price in self.prices):
            raise ValueError("prices must refer to the snapshot month")

        inventory_keys = [(item.region, item.commodity) for item in self.inventories]
        if len(inventory_keys) != len(set(inventory_keys)):
            raise ValueError("inventories must be unique by region and commodity")

        return self
