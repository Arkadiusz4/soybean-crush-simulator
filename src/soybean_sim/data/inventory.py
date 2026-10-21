"""Build initial inventories from marketing-year beginning stocks."""

from collections.abc import Iterable

from soybean_sim.models import Commodity, Inventory, MarketBalance, Region


class InventoryInitializationError(ValueError):
    """Beginning stocks are missing or a region/commodity balance is repeated."""


def build_initial_inventories(
    balances: Iterable[MarketBalance],
) -> tuple[Inventory, ...]:
    """Map beginning stocks to inventories sorted by region and commodity.

    Quantities stay in metric tonnes, including zero; missing stocks and repeated
    region/commodity pairs are invalid. Empty input returns an empty tuple.
    Marketing-year-start stocks are not adjusted to a calendar simulation month.
    Source balances remain unchanged.
    """
    inventories: dict[tuple[Region, Commodity], Inventory] = {}
    for balance in balances:
        key = (balance.region, balance.commodity)
        if key in inventories:
            raise InventoryInitializationError(
                f"Duplicate balance for "
                f"{balance.region.value}/{balance.commodity.value}"
            )

        beginning_stocks = balance.beginning_stocks_tonnes
        if beginning_stocks is None:
            raise InventoryInitializationError(
                f"Missing beginning_stocks_tonnes for "
                f"{balance.region.value}/{balance.commodity.value} "
                f"in marketing year {balance.marketing_year!r}"
            )

        inventories[key] = Inventory(
            region=balance.region,
            commodity=balance.commodity,
            quantity_tonnes=beginning_stocks,
        )

    return tuple(inventories[key] for key in sorted(inventories))
