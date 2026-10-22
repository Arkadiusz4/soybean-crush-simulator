from typing import Any

import pytest
from pydantic import ValidationError

from soybean_sim.agents import TraderConfig


@pytest.fixture
def config() -> TraderConfig:
    """Provide explicit price baselines and a purchase limit with default responses."""
    return TraderConfig(
        reference_soybean_price_usd_per_tonne=400,
        reference_soybean_meal_price_usd_per_tonne=300,
        reference_soybean_oil_price_usd_per_tonne=1000,
        maximum_monthly_purchase_tonnes=1000,
    )


def test_defaults(config: TraderConfig) -> None:
    """Retain the documented price thresholds and opposing purchase/sale responses."""
    assert (config.low_price_ratio, config.high_price_ratio) == (0.90, 1.10)
    assert (
        config.low_purchase_fraction,
        config.normal_purchase_fraction,
        config.high_purchase_fraction,
    ) == (1, 0.5, 0.2)
    assert (
        config.low_sale_fraction,
        config.normal_sale_fraction,
        config.high_sale_fraction,
    ) == (0.2, 0.5, 0.8)


@pytest.mark.parametrize(
    "field",
    [
        "reference_soybean_price_usd_per_tonne",
        "reference_soybean_meal_price_usd_per_tonne",
        "reference_soybean_oil_price_usd_per_tonne",
        "maximum_monthly_purchase_tonnes",
    ],
)
def test_required_values(config: TraderConfig, field: str) -> None:
    """Require callers to supply each reference price and the monthly purchase limit."""
    payload = config.model_dump()
    del payload[field]
    with pytest.raises(ValidationError, match=field):
        TraderConfig.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "reference_soybean_price_usd_per_tonne",
        "reference_soybean_meal_price_usd_per_tonne",
        "reference_soybean_oil_price_usd_per_tonne",
        "maximum_monthly_purchase_tonnes",
        "low_price_ratio",
        "high_price_ratio",
        "low_purchase_fraction",
        "normal_purchase_fraction",
        "high_purchase_fraction",
        "low_sale_fraction",
        "normal_sale_fraction",
        "high_sale_fraction",
    ],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_finite_numbers(config: TraderConfig, field: str, value: float) -> None:
    """Reject nonfinite values in every configured numeric field."""
    with pytest.raises(ValidationError, match=field):
        TraderConfig.model_validate({**config.model_dump(), field: value})


@pytest.mark.parametrize(
    "field",
    [
        "reference_soybean_price_usd_per_tonne",
        "maximum_monthly_purchase_tonnes",
        "normal_sale_fraction",
    ],
)
@pytest.mark.parametrize("value", [True, "1", None])
def test_strict_numeric_types(config: TraderConfig, field: str, value: Any) -> None:
    """Reject booleans, numeric strings, and missing quantities or price settings."""
    with pytest.raises(ValidationError, match=field):
        TraderConfig.model_validate({**config.model_dump(), field: value})


@pytest.mark.parametrize(
    "field",
    [
        "reference_soybean_price_usd_per_tonne",
        "reference_soybean_meal_price_usd_per_tonne",
        "reference_soybean_oil_price_usd_per_tonne",
        "low_price_ratio",
        "high_price_ratio",
    ],
)
@pytest.mark.parametrize("value", [0, -1])
def test_positive_prices_and_ratios(
    config: TraderConfig, field: str, value: float
) -> None:
    """Require positive price baselines and meaningful positive ratio thresholds."""
    with pytest.raises(ValidationError, match=field):
        TraderConfig.model_validate({**config.model_dump(), field: value})


def test_nonnegative_purchase_capacity(config: TraderConfig) -> None:
    """Accept zero purchase capacity while rejecting a negative demand limit."""
    assert (
        TraderConfig.model_validate(
            {**config.model_dump(), "maximum_monthly_purchase_tonnes": 0}
        ).maximum_monthly_purchase_tonnes
        == 0
    )
    with pytest.raises(ValidationError, match="maximum_monthly_purchase_tonnes"):
        TraderConfig.model_validate(
            {**config.model_dump(), "maximum_monthly_purchase_tonnes": -1}
        )


@pytest.mark.parametrize(
    "field",
    [
        "low_purchase_fraction",
        "normal_purchase_fraction",
        "high_purchase_fraction",
        "low_sale_fraction",
        "normal_sale_fraction",
        "high_sale_fraction",
    ],
)
@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_fraction_bounds(config: TraderConfig, field: str, value: float) -> None:
    """Keep purchase and product sale fractions in the inclusive unit interval."""
    with pytest.raises(ValidationError, match=field):
        TraderConfig.model_validate({**config.model_dump(), field: value})


@pytest.mark.parametrize("high_ratio", [0.8, 0.9])
def test_threshold_order(config: TraderConfig, high_ratio: float) -> None:
    """Keep the high threshold strictly above the low threshold."""
    with pytest.raises(ValidationError, match="high_price_ratio must be greater"):
        TraderConfig.model_validate(
            {**config.model_dump(), "high_price_ratio": high_ratio}
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"normal_purchase_fraction": 1, "low_purchase_fraction": 0.5},
        {"high_purchase_fraction": 0.6},
    ],
)
def test_nonincreasing_purchase_fractions(
    config: TraderConfig, changes: dict[str, float]
) -> None:
    """Reject increasing demand fractions as prices enter a higher band."""
    with pytest.raises(
        ValidationError, match="purchase fractions must be nonincreasing"
    ):
        TraderConfig.model_validate({**config.model_dump(), **changes})


@pytest.mark.parametrize(
    "changes", [{"low_sale_fraction": 0.6}, {"high_sale_fraction": 0.4}]
)
def test_nondecreasing_sale_fractions(
    config: TraderConfig, changes: dict[str, float]
) -> None:
    """Reject falling product sale fractions as prices enter a higher band."""
    with pytest.raises(ValidationError, match="sale fractions must be nondecreasing"):
        TraderConfig.model_validate({**config.model_dump(), **changes})


def test_frozen_config_and_serialization(config: TraderConfig) -> None:
    """Round-trip settings while rejecting mutation and execution fields."""
    assert TraderConfig.model_validate_json(config.model_dump_json()) == config
    with pytest.raises(ValidationError, match="frozen_instance"):
        config.low_price_ratio = 0.8
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TraderConfig.model_validate(
            {**config.model_dump(), "destination_region": "brazil"}
        )
