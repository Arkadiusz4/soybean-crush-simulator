"""Select complete historical records without aligning annual and monthly periods."""

from collections.abc import Iterable, Mapping
from datetime import date
from itertools import product

from soybean_sim.models import Commodity, CommodityPrice, MarketBalance, Region

_BalanceKey = tuple[Region, Commodity]


class MarketDataSelectionError(ValueError):
    """A requested historical selection is invalid, incomplete, or conflicting."""


def select_market_data(
    balances: Iterable[MarketBalance],
    prices: Iterable[CommodityPrice],
    *,
    marketing_year: str,
    price_month: date,
    regions: Iterable[Region],
    commodities: Iterable[Commodity],
) -> tuple[tuple[MarketBalance, ...], tuple[CommodityPrice, ...]]:
    """Select a complete set of annual balances and independent monthly prices.

    Require every requested region/commodity pair and one global price per
    commodity. Identical duplicates count once; conflicting selected records
    are invalid. Return balances sorted by region/commodity and prices by
    commodity, preserving original models and unknown quantities.

    Args:
        balances (Iterable[MarketBalance]): Annual regional balances available for
            selection.
        prices (Iterable[CommodityPrice]): Global monthly reference prices available for
            selection.
        marketing_year (str): Source marketing-year label to select.
        price_month (date): First calendar day of the required reference-price month.
        regions (Iterable[Region]): Nonempty selection of required regions; repeated
            identifiers count once.
        commodities (Iterable[Commodity]): Nonempty selection of required commodities;
            repeated identifiers count once.

    Returns:
        tuple[tuple[MarketBalance, ...], tuple[CommodityPrice, ...]]: Selected annual
            balances followed by monthly prices, sorted by region/commodity and
            commodity respectively.
    """
    year_label = _validate_periods(marketing_year, price_month)
    selected_regions, selected_commodities = _normalize_selections(regions, commodities)
    selected_balances = _select_balances(
        balances, year_label, selected_regions, selected_commodities
    )
    _validate_balance_coverage(
        selected_balances, year_label, selected_regions, selected_commodities
    )
    selected_prices = _select_prices(prices, price_month, selected_commodities)
    _validate_price_coverage(selected_prices, price_month, selected_commodities)

    return (
        tuple(selected_balances[key] for key in sorted(selected_balances)),
        tuple(selected_prices[key] for key in sorted(selected_prices)),
    )


def _validate_periods(marketing_year: str, price_month: date) -> str:
    """Validate independent periods and trim the year label like MarketBalance does.

    Args:
        marketing_year (str): Nonempty source marketing-year label.
        price_month (date): First calendar day of the independently selected price
            month.

    Returns:
        str: Marketing-year label with surrounding whitespace removed.
    """
    if not isinstance(marketing_year, str) or not marketing_year.strip():
        raise MarketDataSelectionError("marketing_year must be a nonempty string label")

    if type(price_month) is not date or price_month.day != 1:
        raise MarketDataSelectionError(
            "price_month must be a date on the first day of a month"
        )

    return marketing_year.strip()


def _normalize_selections(
    regions: Iterable[Region], commodities: Iterable[Commodity]
) -> tuple[set[Region], set[Commodity]]:
    """Resolve nonempty enum selections and collapse repeated identifiers.

    Args:
        regions (Iterable[Region]): Required region identifiers.
        commodities (Iterable[Commodity]): Required commodity identifiers.

    Returns:
        tuple[set[Region], set[Commodity]]: Nonempty sets of region and commodity enums
            with duplicates removed.
    """
    if isinstance(regions, (str, bytes)) or isinstance(commodities, (str, bytes)):
        raise MarketDataSelectionError(
            "regions and commodities must be collections of identifiers, not strings"
        )
    try:
        selected_regions = {Region(region) for region in regions}
        selected_commodities = {Commodity(commodity) for commodity in commodities}
    except (TypeError, ValueError) as exc:
        raise MarketDataSelectionError(
            f"Invalid region or commodity selection: {exc}"
        ) from exc

    if not selected_regions:
        raise MarketDataSelectionError("regions must contain at least one region")

    if not selected_commodities:
        raise MarketDataSelectionError(
            "commodities must contain at least one commodity"
        )

    return selected_regions, selected_commodities


def _select_balances(
    balances: Iterable[MarketBalance],
    marketing_year: str,
    regions: set[Region],
    commodities: set[Commodity],
) -> dict[_BalanceKey, MarketBalance]:
    """Keep requested annual records and check duplicates within the selected scope.

    Args:
        balances (Iterable[MarketBalance]): Annual regional records to filter.
        marketing_year (str): Trimmed source marketing-year label to match.
        regions (set[Region]): Required regions.
        commodities (set[Commodity]): Required commodities.

    Returns:
        dict[_BalanceKey, MarketBalance]: Selected records keyed by region and
            commodity, with equal duplicates collapsed.
    """
    selected: dict[_BalanceKey, MarketBalance] = {}
    for balance in balances:
        if (
            balance.marketing_year != marketing_year
            or balance.region not in regions
            or balance.commodity not in commodities
        ):
            continue
        key = (balance.region, balance.commodity)

        previous = selected.get(key)
        if previous is not None and previous != balance:
            raise MarketDataSelectionError(
                f"Conflicting balances for {balance.region.value}/"
                f"{balance.commodity.value}"
                f" in marketing year {marketing_year!r}"
            )
        selected.setdefault(key, balance)

    return selected


def _select_prices(
    prices: Iterable[CommodityPrice], price_month: date, commodities: set[Commodity]
) -> dict[Commodity, CommodityPrice]:
    """Select global commodity prices and check for conflicting records.

    Args:
        prices (Iterable[CommodityPrice]): Global reference-price records to filter.
        price_month (date): Required reference-price month.
        commodities (set[Commodity]): Required commodities.

    Returns:
        dict[Commodity, CommodityPrice]: Selected global prices keyed by commodity, with
            equal duplicates collapsed.
    """
    selected: dict[Commodity, CommodityPrice] = {}
    for price in prices:
        if price.month != price_month or price.commodity not in commodities:
            continue

        previous = selected.get(price.commodity)
        if previous is not None and previous != price:
            raise MarketDataSelectionError(
                f"Conflicting prices for {price.commodity.value} "
                f"in {price_month.isoformat()}"
            )
        selected.setdefault(price.commodity, price)

    return selected


def _validate_balance_coverage(
    balances: Mapping[_BalanceKey, MarketBalance],
    marketing_year: str,
    regions: set[Region],
    commodities: set[Commodity],
) -> None:
    """Check that every explicitly requested region/commodity pair is present.

    Args:
        balances (Mapping[_BalanceKey, MarketBalance]): Selected annual balances keyed
            by region and commodity.
        marketing_year (str): Source year label included in missing-record messages.
        regions (set[Region]): Regions requiring complete commodity coverage.
        commodities (set[Commodity]): Commodities required for every selected region.

    Returns:
        None: No value is returned.
    """
    missing = set(product(regions, commodities)) - balances.keys()
    if missing:
        pairs = ", ".join(
            f"{region.value}/{commodity.value}" for region, commodity in sorted(missing)
        )
        raise MarketDataSelectionError(
            f"Missing balances for marketing year {marketing_year!r}: {pairs}"
        )


def _validate_price_coverage(
    prices: Mapping[Commodity, CommodityPrice],
    price_month: date,
    commodities: set[Commodity],
) -> None:
    """Check global commodity coverage without requiring separate prices per region.

    Args:
        prices (Mapping[Commodity, CommodityPrice]): Selected global reference prices
            keyed by commodity.
        price_month (date): Required month included in missing-record messages.
        commodities (set[Commodity]): Commodities requiring a global price.

    Returns:
        None: No value is returned.
    """
    missing = commodities - prices.keys()
    if missing:
        identifiers = ", ".join(commodity.value for commodity in sorted(missing))
        raise MarketDataSelectionError(
            f"Missing global prices for {price_month.isoformat()}: {identifiers}"
        )
