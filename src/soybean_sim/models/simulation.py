from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .market import CommodityPrice, Inventory, MarketBalance


class SimulationInput(BaseModel):
    """Group annual balances, monthly prices, and initial stocks into a snapshot.

    Collections are nonempty and immutable. Records must be unique and prices share
    one month; matching coverage and marketing-year alignment are not checked.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    balances: Annotated[tuple[MarketBalance, ...], Field(min_length=1)]
    prices: Annotated[tuple[CommodityPrice, ...], Field(min_length=1)]
    inventories: Annotated[tuple[Inventory, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def initial_records_are_unambiguous(self) -> Self:
        """Check record uniqueness and a shared month for all initial prices."""
        balance_keys = [(item.region, item.commodity) for item in self.balances]
        if len(balance_keys) != len(set(balance_keys)):
            raise ValueError("balances must be unique by region and commodity")

        inventory_keys = [(item.region, item.commodity) for item in self.inventories]
        if len(inventory_keys) != len(set(inventory_keys)):
            raise ValueError("inventories must be unique by region and commodity")

        price_keys = [item.commodity for item in self.prices]
        if len(price_keys) != len(set(price_keys)):
            raise ValueError("prices must be unique by commodity")

        if len({item.month for item in self.prices}) != 1:
            raise ValueError("initial prices must refer to the same month")
        return self
