"""Advance calendar months and redate constant prices while carrying stocks."""

from calendar import DECEMBER, JANUARY
from datetime import date

from soybean_sim.market import MarketState, RegionalPrice
from soybean_sim.models import CommodityPrice, MarketSnapshot


def next_month(month: date) -> date:
    """Advance an explicit first-of-month date, including December year transitions.

    Args:
        month (date): First calendar day of the current month.

    Returns:
        date: First calendar day of the following month.
    """
    if type(month) is not date or month.day != 1:
        raise ValueError("month must be a date on the first day of a month")

    if month.month < DECEMBER:
        return date(month.year, month.month + 1, 1)

    if month.year == date.max.year:
        raise ValueError("cannot advance beyond December of the maximum supported year")

    return date(month.year + 1, JANUARY, 1)


def advance_market_month(state: MarketState) -> MarketState:
    """Carry ending stocks into next month's newly dated, unchanged reference prices.

    Args:
        state (MarketState): Completed month's market state with stocks and global
            reference prices.

    Returns:
        MarketState: Next-month state carrying the same stocks, price values, and source
            labels with newly dated price records.
    """
    month = next_month(state.snapshot.month)

    return MarketState(
        snapshot=MarketSnapshot(
            month=month,
            prices=tuple(
                CommodityPrice(
                    commodity=price.commodity,
                    month=month,
                    price_usd_per_tonne=price.price_usd_per_tonne,
                    data_source=price.data_source,
                )
                for price in state.snapshot.prices
            ),
            inventories=state.snapshot.inventories,
        )
    )


def advance_regional_prices(
    prices: tuple[RegionalPrice, ...],
) -> tuple[RegionalPrice, ...]:
    """Advance regional observation dates while preserving values and ordering.

    Args:
        prices: Validated current-month regional series.

    Returns:
        Newly dated regional records for the next calendar month.
    """
    return tuple(
        RegionalPrice(
            month=next_month(price.month),
            region=price.region,
            commodity=price.commodity,
            price_usd_per_tonne=price.price_usd_per_tonne,
        )
        for price in prices
    )
