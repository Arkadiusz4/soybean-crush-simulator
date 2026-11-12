"""Compose selected historical records and beginning stocks into simulation inputs."""

from collections.abc import Iterable
from datetime import date

from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    MarketBalance,
    Region,
    SimulationInput,
)

from . import _inventory, _selection


def build_simulation_input(
    balances: Iterable[MarketBalance],
    prices: Iterable[CommodityPrice],
    *,
    marketing_year: str,
    price_month: date,
    regions: Iterable[Region],
    commodities: Iterable[Commodity],
) -> SimulationInput:
    """Select annual balances and monthly prices, then initialize a validated input.

    Both periods are explicit and independent. Beginning stocks stay in tonnes
    at the marketing-year start, without adjustment to the selected price month.

    Args:
        balances (Iterable[MarketBalance]): Annual regional balances available for
            selection.
        prices (Iterable[CommodityPrice]): Global monthly reference prices available for
            selection.
        marketing_year (str): Source marketing-year label to select independently of the
            price month.
        price_month (date): First calendar day of the required reference-price month.
        regions (Iterable[Region]): Nonempty selection of regions whose balances and
            stocks are required.
        commodities (Iterable[Commodity]): Nonempty selection of commodities requiring
            balances and prices.

    Returns:
        SimulationInput: Validated selected balances, monthly prices, and inventories
            built from beginning stocks.
    """
    selected_balances, selected_prices = _selection.select_market_data(
        balances,
        prices,
        marketing_year=marketing_year,
        price_month=price_month,
        regions=regions,
        commodities=commodities,
    )

    inventories = _inventory.build_initial_inventories(selected_balances)

    return SimulationInput(
        balances=selected_balances,
        prices=selected_prices,
        inventories=inventories,
    )
