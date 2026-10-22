from typing import Any

import pytest
from pydantic import ValidationError

from soybean_sim.agents import ProducerConfig


def test_default_configuration() -> None:
    """Require an explicit reference price and preserve the documented default bands."""
    config = ProducerConfig(reference_price_usd_per_tonne=400)
    assert config.reference_price_usd_per_tonne == 400
    assert (config.low_price_ratio, config.high_price_ratio) == (0.90, 1.10)
    assert (
        config.low_supply_fraction,
        config.normal_supply_fraction,
        config.high_supply_fraction,
    ) == (0.20, 0.50, 0.80)
    with pytest.raises(ValidationError, match="reference_price_usd_per_tonne"):
        ProducerConfig.model_validate({})


@pytest.mark.parametrize(
    "field", ["reference_price_usd_per_tonne", "low_price_ratio", "high_price_ratio"]
)
@pytest.mark.parametrize(
    "value", [0, -1, float("nan"), float("inf"), float("-inf"), True, "1", None]
)
def test_positive_finite_fields(field: str, value: Any) -> None:
    """Reject nonpositive, nonfinite, missing, and coerced price or ratio values."""
    with pytest.raises(ValidationError, match=field):
        ProducerConfig.model_validate(
            {"reference_price_usd_per_tonne": 400, field: value}
        )


@pytest.mark.parametrize(
    "field", ["low_supply_fraction", "normal_supply_fraction", "high_supply_fraction"]
)
@pytest.mark.parametrize(
    "value", [-0.1, 1.1, float("nan"), float("inf"), float("-inf"), True, "0.5", None]
)
def test_bounded_supply_fractions(field: str, value: Any) -> None:
    """Require known finite numeric fractions within the inclusive unit interval."""
    with pytest.raises(ValidationError, match=field):
        ProducerConfig.model_validate(
            {"reference_price_usd_per_tonne": 400, field: value}
        )


@pytest.mark.parametrize("high_ratio", [0.8, 0.9])
def test_price_threshold_order(high_ratio: float) -> None:
    """Keep the high threshold strictly above the low threshold."""
    with pytest.raises(ValidationError, match="high_price_ratio must be greater"):
        ProducerConfig(reference_price_usd_per_tonne=400, high_price_ratio=high_ratio)


@pytest.mark.parametrize(("low", "normal", "high"), [(0.6, 0.5, 0.8), (0.2, 0.9, 0.8)])
def test_supply_fraction_order(low: float, normal: float, high: float) -> None:
    """Reject falling supply fractions as price moves into a higher band."""
    with pytest.raises(ValidationError, match="supply fractions must be nondecreasing"):
        ProducerConfig(
            reference_price_usd_per_tonne=400,
            low_supply_fraction=low,
            normal_supply_fraction=normal,
            high_supply_fraction=high,
        )


def test_frozen_config_and_serialization() -> None:
    """Preserve explicit settings on JSON round trips and prevent silent changes."""
    config = ProducerConfig(reference_price_usd_per_tonne=400)
    assert ProducerConfig.model_validate_json(config.model_dump_json()) == config
    with pytest.raises(ValidationError, match="frozen_instance"):
        config.low_price_ratio = 0.8
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ProducerConfig.model_validate({**config.model_dump(), "harvest_month": 3})
