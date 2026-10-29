from datetime import date, datetime
from itertools import product
from sys import float_info
from typing import cast

import pytest
from pydantic import ValidationError

from soybean_sim import market
from soybean_sim.market import (
    MarketPressure,
    PriceDynamicsConfig,
    PriceDynamicsError,
    RegionalPrice,
    pricing,
    update_regional_prices,
)
from soybean_sim.models import Commodity, Region


@pytest.fixture
def price() -> RegionalPrice:
    """Provide a synthetic regional soybean price in USD per metric tonne."""
    return RegionalPrice(
        month=date(2025, 1, 1),
        region=Region.BRAZIL,
        commodity=Commodity.SOYBEANS,
        price_usd_per_tonne=400,
    )


@pytest.fixture
def pressure() -> MarketPressure:
    """Provide balanced synthetic monthly signals matching the regional price."""
    return MarketPressure(
        month=date(2025, 1, 1),
        region=Region.BRAZIL,
        commodity=Commodity.SOYBEANS,
        supply_tonnes=100,
        demand_tonnes=100,
    )


def test_public_exports() -> None:
    """Expose regional price calculations without changing the simulation API."""
    assert update_regional_prices is pricing.update_regional_prices
    assert PriceDynamicsConfig is pricing.PriceDynamicsConfig
    assert PriceDynamicsError is pricing.PriceDynamicsError
    assert issubclass(PriceDynamicsError, ValueError)
    assert {
        "PriceDynamicsConfig",
        "PriceDynamicsError",
        "update_regional_prices",
    } <= set(market.__all__)


@pytest.mark.parametrize(
    ("supply", "demand", "expected"),
    [
        (100, 100, 400),
        (100, 150, 440),
        (100, 50, 360),
        (0, 0, 400),
        (0, 100, 480),
        (0, 0.5, 440),
        (0.5, 1, 440),
    ],
)
def test_default_supply_demand_response(
    price: RegionalPrice,
    pressure: MarketPressure,
    supply: float,
    demand: float,
    expected: float,
) -> None:
    """Apply the imbalance formula with a denominator floor for small supply signals."""
    observed = pressure.model_copy(
        update={"supply_tonnes": supply, "demand_tonnes": demand}
    )
    result = update_regional_prices([price], [observed], config=PriceDynamicsConfig())
    assert len(result) == 1
    assert result[0].price_usd_per_tonne == pytest.approx(expected)
    assert result[0].month == date(2025, 2, 1)
    assert result[0].region is price.region
    assert result[0].commodity is price.commodity


@pytest.mark.parametrize(("demand", "expected"), [(1000, 440), (0, 360)])
def test_monthly_changes_are_capped_in_both_directions(
    price: RegionalPrice, pressure: MarketPressure, demand: float, expected: float
) -> None:
    """Limit large positive and negative raw responses to the configured fraction."""
    result = update_regional_prices(
        [price],
        [pressure.model_copy(update={"demand_tonnes": demand})],
        config=PriceDynamicsConfig(price_sensitivity=0.8, maximum_monthly_change=0.1),
    )
    assert result[0].price_usd_per_tonne == pytest.approx(expected)


@pytest.mark.parametrize("field", ["price_sensitivity", "maximum_monthly_change"])
def test_zero_response_configuration(
    price: RegionalPrice, pressure: MarketPressure, field: str
) -> None:
    """Leave prices unchanged when sensitivity or the allowed monthly change is zero."""
    config = PriceDynamicsConfig.model_validate({field: 0})
    result = update_regional_prices(
        [price], [pressure.model_copy(update={"demand_tonnes": 1000})], config=config
    )
    assert result[0].price_usd_per_tonne == price.price_usd_per_tonne


@pytest.mark.parametrize(
    ("current", "floor", "expected"), [(0.011, 0.01, 0.01), (1, 5, 5)]
)
def test_minimum_price_takes_precedence_over_monthly_cap(
    price: RegionalPrice,
    pressure: MarketPressure,
    current: float,
    floor: float,
    expected: float,
) -> None:
    """Apply a positive lower bound even when the starting price is below that bound."""
    result = update_regional_prices(
        [price.model_copy(update={"price_usd_per_tonne": current})],
        [pressure.model_copy(update={"demand_tonnes": 0})],
        config=PriceDynamicsConfig(minimum_price_usd_per_tonne=floor),
    )
    assert result[0].price_usd_per_tonne == pytest.approx(expected)


def test_all_regions_and_commodities_are_sorted_without_mutation(
    price: RegionalPrice, pressure: MarketPressure
) -> None:
    """Match individual signals, independent of input order and iterable reuse."""
    prices = []
    pressures = []
    expected = {}
    for index, (region, commodity) in enumerate(product(Region, Commodity)):
        key = {"region": region, "commodity": commodity}
        prices.append(
            price.model_copy(update={**key, "price_usd_per_tonne": 400 + index})
        )
        pressures.append(
            pressure.model_copy(update={**key, "demand_tonnes": 100 + index})
        )
        expected[region, commodity] = (400 + index) * (1 + 0.2 * index / 100)
    original_prices = [item.model_dump_json() for item in prices]
    original_pressures = [item.model_dump_json() for item in pressures]
    config = PriceDynamicsConfig()
    result = update_regional_prices(prices, pressures, config=config)
    assert {
        (item.region, item.commodity): item.price_usd_per_tonne for item in result
    } == pytest.approx(expected)
    assert [(item.region, item.commodity) for item in result] == sorted(expected)
    assert result == update_regional_prices(
        reversed(prices), iter(pressures), config=config
    )
    assert result == update_regional_prices(
        iter(prices), reversed(pressures), config=config
    )
    assert [item.model_dump_json() for item in prices] == original_prices
    assert [item.model_dump_json() for item in pressures] == original_pressures


@pytest.mark.parametrize(
    ("month", "following"),
    [
        (date(2025, 12, 1), date(2026, 1, 1)),
        (date(2024, 2, 1), date(2024, 3, 1)),
        (date(9999, 11, 1), date(9999, 12, 1)),
    ],
)
def test_calendar_advancement(
    price: RegionalPrice, pressure: MarketPressure, month: date, following: date
) -> None:
    """Advance actual calendar months, including rollover and the final valid month."""
    result = update_regional_prices(
        [price.model_copy(update={"month": month})],
        [pressure.model_copy(update={"month": month})],
        config=PriceDynamicsConfig(),
    )
    assert result[0].month == following


def test_calendar_overflow_is_rejected(
    price: RegionalPrice, pressure: MarketPressure
) -> None:
    """Report a domain error rather than advancing beyond Python's supported years."""
    month = date(date.max.year, 12, 1)
    with pytest.raises(PriceDynamicsError, match="maximum supported year"):
        update_regional_prices(
            [price.model_copy(update={"month": month})],
            [pressure.model_copy(update={"month": month})],
            config=PriceDynamicsConfig(),
        )


@pytest.mark.parametrize("records", ["prices", "pressures"])
@pytest.mark.parametrize("duplicate", ["same", "equal", "conflicting"])
def test_duplicate_keys_are_always_rejected(
    price: RegionalPrice, pressure: MarketPressure, records: str, duplicate: str
) -> None:
    """Reject both identical and conflicting repeated observations for either input."""
    prices = [price]
    pressures = [pressure]
    if records == "prices":
        duplicates = {
            "same": price,
            "equal": price.model_copy(),
            "conflicting": price.model_copy(update={"price_usd_per_tonne": 401}),
        }
        prices.append(duplicates[duplicate])
    else:
        duplicates = {
            "same": pressure,
            "equal": pressure.model_copy(),
            "conflicting": pressure.model_copy(update={"demand_tonnes": 101}),
        }
        pressures.append(duplicates[duplicate])
    with pytest.raises(
        PriceDynamicsError, match=r"Duplicate.*brazil/soybeans.*2025-01-01"
    ):
        update_regional_prices(prices, pressures, config=PriceDynamicsConfig())


@pytest.mark.parametrize(
    "defect", ["missing", "extra", "region", "commodity", "month", "no_prices"]
)
def test_pressure_coverage_must_match_exactly(
    price: RegionalPrice, pressure: MarketPressure, defect: str
) -> None:
    """Require exactly one pressure per month, region, and commodity price key."""
    prices = [price]
    pressures = [pressure]
    if defect == "missing":
        pressures = []
    elif defect == "extra":
        pressures.append(pressure.model_copy(update={"region": Region.ARGENTINA}))
    elif defect == "no_prices":
        prices = []
    else:
        changes = {
            "region": Region.ARGENTINA,
            "commodity": Commodity.SOYBEAN_OIL,
            "month": date(2025, 2, 1),
        }
        pressures = [pressure.model_copy(update={defect: changes[defect]})]
    with pytest.raises(PriceDynamicsError, match="Pressure coverage mismatch"):
        update_regional_prices(prices, pressures, config=PriceDynamicsConfig())


def test_mixed_price_months_are_rejected(
    price: RegionalPrice, pressure: MarketPressure
) -> None:
    """Do not update several starting months in one pricing call."""
    changes = {"region": Region.ARGENTINA, "month": date(2025, 2, 1)}
    with pytest.raises(PriceDynamicsError, match="same month"):
        update_regional_prices(
            [price, price.model_copy(update=changes)],
            [pressure, pressure.model_copy(update=changes)],
            config=PriceDynamicsConfig(),
        )


@pytest.mark.parametrize("records", ["prices", "pressures"])
@pytest.mark.parametrize(
    "month", [date(2025, 1, 2), datetime(2025, 1, 1), "2025-01-01"]
)
def test_unchecked_months_are_revalidated(
    price: RegionalPrice, pressure: MarketPressure, records: str, month: object
) -> None:
    """Reject non-normalized or coerced month values copied without model validation."""
    prices = (
        [price.model_copy(update={"month": month})] if records == "prices" else [price]
    )
    pressures = (
        [pressure.model_copy(update={"month": month})]
        if records == "pressures"
        else [pressure]
    )
    with pytest.raises(PriceDynamicsError, match="month"):
        update_regional_prices(prices, pressures, config=PriceDynamicsConfig())


@pytest.mark.parametrize("records", ["prices", "pressures"])
def test_wrong_record_types_are_rejected(
    price: RegionalPrice, pressure: MarketPressure, records: str
) -> None:
    """Require typed observations rather than implicitly interpreting dictionaries."""
    prices = [cast(RegionalPrice, {})] if records == "prices" else [price]
    pressures = [cast(MarketPressure, {})] if records == "pressures" else [pressure]
    with pytest.raises(PriceDynamicsError, match="Expected"):
        update_regional_prices(prices, pressures, config=PriceDynamicsConfig())


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "100"])
def test_unchecked_quantities_are_revalidated(
    price: RegionalPrice, pressure: MarketPressure, value: object
) -> None:
    """Keep invalid supply quantities out of the price response calculation."""
    with pytest.raises(PriceDynamicsError, match="supply_tonnes"):
        update_regional_prices(
            [price],
            [pressure.model_copy(update={"supply_tonnes": value})],
            config=PriceDynamicsConfig(),
        )


def test_nonfinite_next_price_is_rejected(
    price: RegionalPrice, pressure: MarketPressure
) -> None:
    """Report overflow after bounded changes rather than returning an infinite price."""
    with pytest.raises(
        PriceDynamicsError, match=r"Non-finite next price.*brazil/soybeans"
    ):
        update_regional_prices(
            [price.model_copy(update={"price_usd_per_tonne": float_info.max})],
            [pressure.model_copy(update={"demand_tonnes": 200})],
            config=PriceDynamicsConfig(),
        )


@pytest.mark.parametrize("sensitivity", [0, 0.2])
def test_extreme_imbalance_remains_bounded(
    price: RegionalPrice, pressure: MarketPressure, sensitivity: float
) -> None:
    """Cap huge finite input ratios without letting zero sensitivity create NaN."""
    result = update_regional_prices(
        [price],
        [
            pressure.model_copy(
                update={"supply_tonnes": 0, "demand_tonnes": float_info.max}
            )
        ],
        config=PriceDynamicsConfig(
            price_sensitivity=sensitivity, supply_floor_tonnes=1e-300
        ),
    )
    assert result[0].price_usd_per_tonne == pytest.approx(
        400 if sensitivity == 0 else 480
    )


@pytest.mark.parametrize(
    "field",
    [
        "price_sensitivity",
        "maximum_monthly_change",
        "minimum_price_usd_per_tonne",
        "supply_floor_tonnes",
    ],
)
@pytest.mark.parametrize(
    "value", [-1, float("nan"), float("inf"), float("-inf"), True, "0.2", None]
)
def test_invalid_configuration_values(field: str, value: object) -> None:
    """Require finite numeric parameters with their declared signs and strict types."""
    with pytest.raises(ValidationError, match=field):
        PriceDynamicsConfig.model_validate({field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maximum_monthly_change", 1),
        ("maximum_monthly_change", 1.1),
        ("minimum_price_usd_per_tonne", 0),
        ("supply_floor_tonnes", 0),
    ],
)
def test_configuration_bounds(field: str, value: float) -> None:
    """Exclude complete monthly price drops and nonpositive numerical floors."""
    with pytest.raises(ValidationError, match=field):
        PriceDynamicsConfig.model_validate({field: value})


def test_default_config_is_frozen_and_serializable() -> None:
    """Preserve documented defaults, reject extras, and prevent parameter mutation."""
    config = PriceDynamicsConfig()
    assert config.model_dump() == {
        "price_sensitivity": 0.2,
        "maximum_monthly_change": 0.2,
        "minimum_price_usd_per_tonne": 0.01,
        "supply_floor_tonnes": 1.0,
    }
    assert PriceDynamicsConfig.model_validate_json(config.model_dump_json()) == config
    with pytest.raises(ValidationError, match="frozen_instance"):
        config.price_sensitivity = 0.5
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PriceDynamicsConfig.model_validate({"transport_cost": 5})


def test_unchecked_config_copy_is_revalidated(
    price: RegionalPrice, pressure: MarketPressure
) -> None:
    """Translate invalid copied settings into the pricing function's domain error."""
    config = PriceDynamicsConfig().model_copy(update={"maximum_monthly_change": 1})
    with pytest.raises(
        PriceDynamicsError, match="Invalid price dynamics configuration"
    ):
        update_regional_prices([price], [pressure], config=config)


def test_wrong_config_type_is_rejected() -> None:
    """Require the explicit pricing configuration even when both inputs are empty."""
    with pytest.raises(
        PriceDynamicsError, match="config must be a PriceDynamicsConfig"
    ):
        update_regional_prices([], [], config=cast(PriceDynamicsConfig, None))


def test_empty_inputs_return_no_prices() -> None:
    """Keep empty input collections empty rather than inventing regional markets."""
    assert (
        update_regional_prices(iter(()), iter(()), config=PriceDynamicsConfig()) == ()
    )
