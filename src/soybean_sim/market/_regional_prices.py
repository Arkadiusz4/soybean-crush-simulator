"""Initialize regional prices and adapt them into temporary agent observations."""

from collections.abc import Iterable
from typing import Final

from pydantic import ValidationError

from soybean_sim.models import Commodity, CommodityPrice, MarketSnapshot, Region

from ._contracts import RegionalPrice

_REGIONAL_PRICE_SOURCE: Final = "simulated_regional"
_PriceKey = tuple[Region, Commodity]


class RegionalPriceError(ValueError):
    """Invalid regional price data or incomplete price coverage."""


def initialize_regional_prices(
    snapshot: MarketSnapshot, regions: Iterable[Region]
) -> tuple[RegionalPrice, ...]:
    """Copy global price values to each requested region.

    Args:
        snapshot (MarketSnapshot): Global snapshot with all three commodity prices.
        regions (Iterable[Region]): Nonempty collection of unique regions.

    Returns:
        tuple[RegionalPrice, ...]: Initial prices sorted by region and commodity.
    """
    initial = _validate_snapshot(snapshot)
    selected_regions = _validate_regions(regions)

    missing = set(Commodity) - {price.commodity for price in initial.prices}
    if missing:
        raise RegionalPriceError(
            "Missing global commodity prices: "
            + ", ".join(commodity.value for commodity in sorted(missing))
        )

    prices = sorted(initial.prices, key=lambda price: price.commodity)

    return tuple(
        RegionalPrice(
            month=initial.month,
            region=region,
            commodity=price.commodity,
            price_usd_per_tonne=price.price_usd_per_tonne,
        )
        for region in selected_regions
        for price in prices
    )


def snapshot_for_region(
    snapshot: MarketSnapshot,
    regional_prices: Iterable[RegionalPrice],
    region: Region,
) -> MarketSnapshot:
    """Create a regional agent view, preserving month and all inventories.

    Args:
        snapshot (MarketSnapshot): Original global observations and inventories.
        regional_prices (Iterable[RegionalPrice]): Valid records covering the
            requested region's commodities; other regions are allowed.
        region (Region): Region whose simulated prices the agent should observe.

    Returns:
        MarketSnapshot: Temporary agent view using simulated regional prices.
    """
    initial = _validate_snapshot(snapshot)
    selected_region = _validate_region(region)
    indexed = _index_prices(regional_prices, initial)
    selected = {
        commodity: price
        for (price_region, commodity), price in indexed.items()
        if price_region == selected_region
    }

    missing = {price.commodity for price in initial.prices} - selected.keys()
    if missing:
        raise RegionalPriceError(
            f"Missing regional prices for {selected_region.value}: "
            + ", ".join(commodity.value for commodity in sorted(missing))
        )

    return MarketSnapshot(
        month=initial.month,
        inventories=initial.inventories,
        prices=tuple(
            CommodityPrice(
                commodity=commodity,
                month=initial.month,
                price_usd_per_tonne=price.price_usd_per_tonne,
                data_source=_REGIONAL_PRICE_SOURCE,
            )
            for commodity, price in sorted(selected.items())
        ),
    )


def _validate_snapshot(snapshot: MarketSnapshot) -> MarketSnapshot:
    """Revalidate the snapshot's prices, dates, and inventories.

    Args:
        snapshot (MarketSnapshot): Candidate market snapshot.

    Returns:
        MarketSnapshot: Snapshot with revalidated nested observations.
    """
    if not isinstance(snapshot, MarketSnapshot):
        raise RegionalPriceError("snapshot must be a MarketSnapshot")

    try:
        return MarketSnapshot.model_validate(snapshot.model_dump(warnings=False))
    except ValidationError as exc:
        raise RegionalPriceError(f"Invalid market snapshot: {exc}") from exc


def _validate_region(region: Region) -> Region:
    """Resolve a supported region identifier.

    Args:
        region (Region): Region enum or supported serialized identifier.

    Returns:
        Region: Validated region enum member.
    """
    try:
        return Region(region)
    except (TypeError, ValueError) as exc:
        raise RegionalPriceError(f"Invalid region: {region!r}") from exc


def _validate_regions(regions: Iterable[Region]) -> tuple[Region, ...]:
    """Validate and sort a nonempty collection of unique regions.

    Args:
        regions (Iterable[Region]): Requested region identifiers.

    Returns:
        tuple[Region, ...]: Validated regions in sorted order.
    """
    if isinstance(regions, (str, bytes)):
        raise RegionalPriceError("regions must be a collection of region identifiers")

    try:
        selected = tuple(_validate_region(region) for region in regions)
    except TypeError as exc:
        raise RegionalPriceError(
            "regions must be an iterable of region identifiers"
        ) from exc

    if not selected:
        raise RegionalPriceError("regions must contain at least one region")

    if len(set(selected)) != len(selected):
        raise RegionalPriceError("regions must not contain duplicate identifiers")

    return tuple(sorted(selected))


def _index_prices(
    prices: Iterable[RegionalPrice], snapshot: MarketSnapshot
) -> dict[_PriceKey, RegionalPrice]:
    """Validate all regional observations against the global month and commodities.

    Args:
        prices (Iterable[RegionalPrice]): Candidate regional observations.
        snapshot (MarketSnapshot): Required month and commodity coverage.

    Returns:
        dict[tuple[Region, Commodity], RegionalPrice]: Unique prices by regional key.
    """
    try:
        observations = iter(prices)
    except TypeError as exc:
        raise RegionalPriceError(
            "regional_prices must be an iterable of prices"
        ) from exc

    commodities = {price.commodity for price in snapshot.prices}
    indexed: dict[_PriceKey, RegionalPrice] = {}
    for price in observations:
        if not isinstance(price, RegionalPrice):
            raise RegionalPriceError(
                "regional_prices must contain RegionalPrice records"
            )
        try:
            validated = RegionalPrice.model_validate(price.model_dump(warnings=False))
        except ValidationError as exc:
            raise RegionalPriceError(f"Invalid regional price: {exc}") from exc

        if validated.month != snapshot.month:
            raise RegionalPriceError(
                f"Regional price month {validated.month} does not match "
                f"snapshot month {snapshot.month}"
            )

        if validated.commodity not in commodities:
            raise RegionalPriceError(
                f"Unexpected regional commodity {validated.commodity.value} "
                f"for {validated.region.value}"
            )

        key = (validated.region, validated.commodity)
        if key in indexed:
            raise RegionalPriceError(
                f"Duplicate regional price for "
                f"{validated.region.value}/{validated.commodity.value}"
            )

        indexed[key] = validated

    return indexed
