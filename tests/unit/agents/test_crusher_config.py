from typing import Any

import pytest
from pydantic import ValidationError

from soybean_sim.agents import CrusherConfig


def test_defaults_and_required_inputs() -> None:
    """Require capacity and costs while retaining documented response defaults."""
    config = CrusherConfig(
        monthly_capacity_tonnes=1000, processing_cost_usd_per_tonne=0
    )
    assert (config.meal_yield_fraction, config.oil_yield_fraction) == (0.8, 0.2)
    assert (
        config.low_margin_threshold_usd_per_tonne,
        config.high_margin_threshold_usd_per_tonne,
        config.full_utilization_threshold_usd_per_tonne,
    ) == (0, 30, 60)
    assert (
        config.low_utilization_fraction,
        config.medium_utilization_fraction,
        config.high_utilization_fraction,
    ) == (0.3, 0.6, 1)
    for field in ("monthly_capacity_tonnes", "processing_cost_usd_per_tonne"):
        payload = config.model_dump()
        del payload[field]
        with pytest.raises(ValidationError, match=field):
            CrusherConfig.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "monthly_capacity_tonnes",
        "processing_cost_usd_per_tonne",
        "meal_yield_fraction",
        "oil_yield_fraction",
        "low_margin_threshold_usd_per_tonne",
        "high_margin_threshold_usd_per_tonne",
        "full_utilization_threshold_usd_per_tonne",
        "low_utilization_fraction",
        "medium_utilization_fraction",
        "high_utilization_fraction",
    ],
)
@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), float("-inf"), True, "1", None]
)
def test_finite_numeric_fields(field: str, value: Any) -> None:
    """Require finite known numbers without accepting strings or booleans."""
    with pytest.raises(ValidationError, match=field):
        CrusherConfig.model_validate(
            {
                "monthly_capacity_tonnes": 1000,
                "processing_cost_usd_per_tonne": 10,
                field: value,
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("monthly_capacity_tonnes", 0),
        ("monthly_capacity_tonnes", -1),
        ("processing_cost_usd_per_tonne", -1),
        ("meal_yield_fraction", -0.1),
        ("oil_yield_fraction", -0.1),
    ],
)
def test_capacity_cost_and_yield_bounds(field: str, value: float) -> None:
    """Reject nonpositive capacity and negative costs or yields."""
    with pytest.raises(ValidationError, match=field):
        CrusherConfig.model_validate(
            {
                "monthly_capacity_tonnes": 1000,
                "processing_cost_usd_per_tonne": 10,
                field: value,
            }
        )


@pytest.mark.parametrize(
    ("meal", "oil"), [(0, 0), (0.8, 0.1), (0.8, 0.3), (0.8, 0.200000002)]
)
def test_yields_must_sum_to_one(meal: float, oil: float) -> None:
    """Reject material shortages or excesses in the simplified two-product yields."""
    with pytest.raises(ValidationError, match="yield fractions must sum to 1"):
        CrusherConfig(
            monthly_capacity_tonnes=1000,
            processing_cost_usd_per_tonne=10,
            meal_yield_fraction=meal,
            oil_yield_fraction=oil,
        )


@pytest.mark.parametrize(
    ("meal", "oil"), [(0, 1), (1, 0), (0.5, 0.5), (0.8, 0.2000000005)]
)
def test_yield_endpoints_and_tolerance(meal: float, oil: float) -> None:
    """Accept nonnegative endpoints and sum differences within absolute tolerance."""
    config = CrusherConfig(
        monthly_capacity_tonnes=1000,
        processing_cost_usd_per_tonne=10,
        meal_yield_fraction=meal,
        oil_yield_fraction=oil,
    )
    assert config.meal_yield_fraction == meal
    assert config.oil_yield_fraction == oil


@pytest.mark.parametrize(
    "changes",
    [
        {"high_margin_threshold_usd_per_tonne": 0},
        {"full_utilization_threshold_usd_per_tonne": 30},
        {"low_margin_threshold_usd_per_tonne": 40},
    ],
)
def test_strict_threshold_order(changes: dict[str, float]) -> None:
    """Reject equal or decreasing boundaries between margin bands."""
    with pytest.raises(
        ValidationError, match="margin thresholds must be strictly increasing"
    ):
        CrusherConfig.model_validate(
            {
                "monthly_capacity_tonnes": 1000,
                "processing_cost_usd_per_tonne": 10,
                **changes,
            }
        )


@pytest.mark.parametrize(
    "field",
    [
        "low_utilization_fraction",
        "medium_utilization_fraction",
        "high_utilization_fraction",
    ],
)
@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_utilization_bounds(field: str, value: float) -> None:
    """Reject requested capacity fractions outside the inclusive unit interval."""
    with pytest.raises(ValidationError, match=field):
        CrusherConfig.model_validate(
            {
                "monthly_capacity_tonnes": 1000,
                "processing_cost_usd_per_tonne": 10,
                field: value,
            }
        )


@pytest.mark.parametrize(("low", "medium", "high"), [(0.7, 0.6, 1), (0.3, 0.9, 0.8)])
def test_utilization_order(low: float, medium: float, high: float) -> None:
    """Reject falling utilization as the net margin enters a higher band."""
    with pytest.raises(
        ValidationError, match="utilization fractions must be nondecreasing"
    ):
        CrusherConfig(
            monthly_capacity_tonnes=1000,
            processing_cost_usd_per_tonne=10,
            low_utilization_fraction=low,
            medium_utilization_fraction=medium,
            high_utilization_fraction=high,
        )


def test_frozen_config_and_serialization() -> None:
    """Round-trip settings while rejecting mutation and speculative extra fields."""
    config = CrusherConfig(
        monthly_capacity_tonnes=1000, processing_cost_usd_per_tonne=10
    )
    assert CrusherConfig.model_validate_json(config.model_dump_json()) == config
    with pytest.raises(ValidationError, match="frozen_instance"):
        config.monthly_capacity_tonnes = 500
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CrusherConfig.model_validate(
            {**config.model_dump(), "soybean_owner": "crusher-1"}
        )
