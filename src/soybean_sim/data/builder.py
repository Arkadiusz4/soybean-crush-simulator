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

from . import inventory, selection


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
    """
    selected_balances, selected_prices = selection.select_market_data(
        balances,
        prices,
        marketing_year=marketing_year,
        price_month=price_month,
        regions=regions,
        commodities=commodities,
    )

    inventories = inventory.build_initial_inventories(selected_balances)

    return SimulationInput(
        balances=selected_balances,
        prices=selected_prices,
        inventories=inventories,
    )
