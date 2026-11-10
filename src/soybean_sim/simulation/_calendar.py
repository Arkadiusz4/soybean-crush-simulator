"""Advance calendar months and redate constant prices while carrying stocks."""

from calendar import DECEMBER, JANUARY
from datetime import date

from soybean_sim.market import MarketState
from soybean_sim.models import CommodityPrice, MarketSnapshot


def next_month(month: date) -> date:
    """Advance an explicit first-of-month date, including December year transitions."""
    if type(month) is not date or month.day != 1:
        raise ValueError("month must be a date on the first day of a month")

    if month.month < DECEMBER:
        return date(month.year, month.month + 1, 1)

    if month.year == date.max.year:
        raise ValueError("cannot advance beyond December of the maximum supported year")

    return date(month.year + 1, JANUARY, 1)


def advance_market_month(state: MarketState) -> MarketState:
    """Carry ending stocks into next month's newly dated, unchanged reference prices."""
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
