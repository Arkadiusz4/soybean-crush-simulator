"""Calculate regional prices with a bounded heuristic, independent of execution."""

import math
from calendar import DECEMBER, JANUARY
from collections.abc import Iterable
from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from soybean_sim.models import Commodity, Region

from .contracts import MarketPressure, RegionalPrice

_NonNegativeFinite = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
_PositiveFinite = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
_MonthlyChange = Annotated[float, Field(strict=True, ge=0, lt=1, allow_inf_nan=False)]
_PriceKey = tuple[date, Region, Commodity]


class PriceDynamicsConfig(BaseModel):
    """Set sensitivity, a fractional monthly cap, and positive price/supply floors.

    These are simulation assumptions, not empirically calibrated parameters.
    Price floors use USD/tonne; the supply floor uses metric tonnes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    price_sensitivity: _NonNegativeFinite = 0.2
    maximum_monthly_change: _MonthlyChange = 0.2
    minimum_price_usd_per_tonne: _PositiveFinite = 0.01
    supply_floor_tonnes: _PositiveFinite = 1.0


class PriceDynamicsError(ValueError):
    """Regional price inputs, coverage, calendar advancement, or calculations fail."""


def update_regional_prices(
    current_prices: Iterable[RegionalPrice],
    pressures: Iterable[MarketPressure],
    *,
    config: PriceDynamicsConfig,
) -> tuple[RegionalPrice, ...]:
    """Return next-month prices sorted by region and commodity without mutation.

    Require one shared price month and exactly one pressure per price key.
    Apply sensitivity to (demand - supply) / max(supply, supply floor), cap the
    fractional change, then apply the price floor. The floor takes precedence
    over the change cap. Empty prices and pressures return an empty tuple.
    """
    validated_config = _validate_config(config)
    prices_by_key = _index_records(current_prices, RegionalPrice)
    pressures_by_key = _index_records(pressures, MarketPressure)
    month = _matching_month(prices_by_key, pressures_by_key)
    if month is None:
        return ()

    following_month = _next_month(month)
    return tuple(
        RegionalPrice(
            month=following_month,
            region=price.region,
            commodity=price.commodity,
            price_usd_per_tonne=_updated_price(
                price, pressures_by_key[key], validated_config
            ),
        )
        for key, price in sorted(prices_by_key.items())
    )


def _validate_config(config: PriceDynamicsConfig) -> PriceDynamicsConfig:
    """Revalidate configuration copies and report invalid settings as domain errors."""
    if not isinstance(config, PriceDynamicsConfig):
        raise PriceDynamicsError("config must be a PriceDynamicsConfig")
    try:
        return PriceDynamicsConfig.model_validate(config.model_dump(warnings=False))
    except ValidationError as exc:
        raise PriceDynamicsError(
            f"Invalid price dynamics configuration: {exc}"
        ) from exc


def _index_records[Record: (RegionalPrice, MarketPressure)](
    records: Iterable[Record], record_type: type[Record]
) -> dict[_PriceKey, Record]:
    """Revalidate records and reject every repeated month/region/commodity key."""
    indexed: dict[_PriceKey, Record] = {}
    for record in records:
        if not isinstance(record, record_type):
            raise PriceDynamicsError(f"Expected {record_type.__name__} records")
        try:
            validated = record_type.model_validate(record.model_dump(warnings=False))
        except ValidationError as exc:
            raise PriceDynamicsError(f"Invalid {record_type.__name__}: {exc}") from exc

        key = (validated.month, validated.region, validated.commodity)
        if key in indexed:
            raise PriceDynamicsError(
                f"Duplicate {record_type.__name__} for {validated.region.value}/"
                f"{validated.commodity.value} in {validated.month.isoformat()}"
            )

        indexed[key] = validated

    return indexed


def _matching_month(
    prices: dict[_PriceKey, RegionalPrice], pressures: dict[_PriceKey, MarketPressure]
) -> date | None:
    """Require one price month and exact pressure coverage, including empty inputs."""
    months = {month for month, _, _ in prices}
    if len(months) > 1:
        raise PriceDynamicsError("All current prices must refer to the same month")

    missing = prices.keys() - pressures.keys()
    extra = pressures.keys() - prices.keys()
    if missing or extra:
        raise PriceDynamicsError(
            f"Pressure coverage mismatch: missing={sorted(missing)!r}; "
            f"extra={sorted(extra)!r}"
        )

    return next(iter(months), None)


def _next_month(month: date) -> date:
    """Advance a validated month locally to avoid depending on the simulation engine."""
    if month.month == DECEMBER:
        if month.year == date.max.year:
            raise PriceDynamicsError("Cannot advance beyond the maximum supported year")
        return date(month.year + 1, JANUARY, 1)

    return date(month.year, month.month + 1, 1)


def _updated_price(
    price: RegionalPrice, pressure: MarketPressure, config: PriceDynamicsConfig
) -> float:
    """Bound the supply/demand response and reject overflow in the resulting price."""
    raw_change = 0.0
    if config.price_sensitivity > 0:
        imbalance = (pressure.demand_tonnes - pressure.supply_tonnes) / max(
            pressure.supply_tonnes, config.supply_floor_tonnes
        )
        raw_change = config.price_sensitivity * imbalance

    bounded_change = max(
        -config.maximum_monthly_change,
        min(config.maximum_monthly_change, raw_change),
    )
    next_price = max(
        config.minimum_price_usd_per_tonne,
        price.price_usd_per_tonne * (1 + bounded_change),
    )
    if not math.isfinite(next_price):
        raise PriceDynamicsError(
            f"Non-finite next price for {price.region.value}/{price.commodity.value} "
            f"after {price.month.isoformat()}"
        )

    return next_price
