from datetime import date, datetime
from itertools import product
from sys import float_info
from typing import cast

import pytest
from pydantic import ValidationError

from soybean_sim import market
from soybean_sim.market import (
    MarketFlowError,
    MarketFlowType,
    MonthlyFlowConfig,
    flows,
    generate_monthly_flows,
)
from soybean_sim.models import Commodity, MarketBalance, Region


@pytest.fixture
def balance() -> MarketBalance:
    """Provide synthetic annual quantities with unrelated fields explicitly present."""
    return MarketBalance(
        region=Region.BRAZIL,
        commodity=Commodity.SOYBEANS,
        marketing_year="2024",
        production_tonnes=1200,
        imports_tonnes=240,
        exports_tonnes=120,
        domestic_consumption_tonnes=600,
        beginning_stocks_tonnes=60,
        ending_stocks_tonnes=50,
        crush_tonnes=400,
    )


@pytest.fixture
def balances(balance: MarketBalance) -> list[MarketBalance]:
    """Cover every supported region and commodity with comparable synthetic values."""
    return [
        balance.model_copy(
            update={"region": region, "commodity": commodity, "crush_tonnes": None}
        )
        for region, commodity in product(Region, Commodity)
    ]


def test_public_exports() -> None:
    """Expose monthly allocation independently of the state and settlement APIs."""
    assert generate_monthly_flows is flows.generate_monthly_flows
    assert MonthlyFlowConfig is flows.MonthlyFlowConfig
    assert MarketFlowError is flows.MarketFlowError
    assert issubclass(MarketFlowError, ValueError)
    assert {"generate_monthly_flows", "MonthlyFlowConfig", "MarketFlowError"} <= set(
        market.__all__
    )


@pytest.mark.parametrize("month_number", range(1, 13))
def test_uniform_supply_allocation(balance: MarketBalance, month_number: int) -> None:
    """Allocate one twelfth of annual soybean production in every calendar month."""
    month = date(2025, month_number, 1)
    result = generate_monthly_flows([balance], month=month, config=MonthlyFlowConfig())
    assert len(result) == 1
    flow = result[0]
    assert flow.month == month
    assert flow.region is Region.BRAZIL
    assert flow.commodity is Commodity.SOYBEANS
    assert flow.flow_type is MarketFlowType.SUPPLY
    assert flow.quantity_tonnes == pytest.approx(100)


@pytest.mark.parametrize(
    ("month_number", "expected_supply", "expected_consumption"),
    [(1, 300, 360), (12, 900, 240), (6, 0, 0)],
)
def test_custom_weights_use_calendar_month_indices(
    balance: MarketBalance,
    month_number: int,
    expected_supply: float,
    expected_consumption: float,
) -> None:
    """Use independent January and December weights, retaining zero-weight flows."""
    config = MonthlyFlowConfig(
        supply_weights=(0.25, *([0.0] * 10), 0.75),
        consumption_weights=(0.6, *([0.0] * 10), 0.4),
    )
    meal = balance.model_copy(
        update={"commodity": Commodity.SOYBEAN_MEAL, "crush_tonnes": None}
    )
    result = generate_monthly_flows(
        [meal], month=date(2025, month_number, 1), config=config
    )
    assert {flow.flow_type: flow.quantity_tonnes for flow in result} == pytest.approx(
        {
            MarketFlowType.SUPPLY: expected_supply,
            MarketFlowType.CONSUMPTION: expected_consumption,
        }
    )


def test_all_regions_and_commodities_have_expected_flows(
    balances: list[MarketBalance],
) -> None:
    """Include meal/oil consumption but exclude soybean consumption and trade flows."""
    result = generate_monthly_flows(
        balances, month=date(2025, 1, 1), config=MonthlyFlowConfig()
    )
    expected = {
        (region, commodity, MarketFlowType.SUPPLY): 100
        for region, commodity in product(Region, Commodity)
    }
    expected.update(
        {
            (region, commodity, MarketFlowType.CONSUMPTION): 50
            for region, commodity in product(
                Region, (Commodity.SOYBEAN_MEAL, Commodity.SOYBEAN_OIL)
            )
        }
    )
    assert {
        (flow.region, flow.commodity, flow.flow_type): flow.quantity_tonnes
        for flow in result
    } == pytest.approx(expected)


@pytest.mark.parametrize("commodity", list(Commodity))
@pytest.mark.parametrize(
    ("production", "consumption"), [(None, None), (None, 600), (1200, None), (0, 0)]
)
def test_missing_values_are_skipped_and_explicit_zeros_preserved(
    balance: MarketBalance,
    commodity: Commodity,
    production: float | None,
    consumption: float | None,
) -> None:
    """Keep unknown annual observations distinct from observed zero quantities."""
    annual = balance.model_copy(
        update={
            "commodity": commodity,
            "production_tonnes": production,
            "domestic_consumption_tonnes": consumption,
            "crush_tonnes": None,
        }
    )
    result = generate_monthly_flows(
        [annual], month=date(2025, 1, 1), config=MonthlyFlowConfig()
    )
    expected = {}
    if production is not None:
        expected[MarketFlowType.SUPPLY] = production / 12
    if commodity != Commodity.SOYBEANS and consumption is not None:
        expected[MarketFlowType.CONSUMPTION] = consumption / 12
    assert {flow.flow_type: flow.quantity_tonnes for flow in result} == pytest.approx(
        expected
    )


def test_ordering_iterators_and_source_immutability(
    balances: list[MarketBalance],
) -> None:
    """Keep stable region/commodity/type ordering for lists and iterators."""
    original = [balance.model_dump_json() for balance in balances]
    config = MonthlyFlowConfig()
    result = generate_monthly_flows(balances, month=date(2025, 1, 1), config=config)
    reordered = generate_monthly_flows(
        reversed(balances), month=date(2025, 1, 1), config=config
    )
    assert result == reordered
    keys = [(flow.region, flow.commodity, flow.flow_type) for flow in result]
    assert keys == sorted(keys)
    assert [balance.model_dump_json() for balance in balances] == original


def test_equal_duplicates_are_collapsed(balance: MarketBalance) -> None:
    """Count repeated instances and equal independent records only once."""
    config = MonthlyFlowConfig()
    assert generate_monthly_flows(
        [balance, balance, balance.model_copy()], month=date(2025, 1, 1), config=config
    ) == generate_monthly_flows([balance], month=date(2025, 1, 1), config=config)


@pytest.mark.parametrize("field", ["production_tonnes", "imports_tonnes"])
def test_conflicting_duplicates_are_rejected(
    balance: MarketBalance, field: str
) -> None:
    """Reject conflicting records even when the differing field is not allocated."""
    conflicting = balance.model_copy(update={field: 999})
    with pytest.raises(MarketFlowError, match=r"Conflicting.*brazil/soybeans.*2024"):
        generate_monthly_flows(
            [balance, conflicting], month=date(2025, 1, 1), config=MonthlyFlowConfig()
        )


def test_mixed_years_are_rejected_even_without_observed_flows(
    balance: MarketBalance,
) -> None:
    """Do not silently combine annual periods when all allocated values are missing."""
    missing = balance.model_copy(
        update={"production_tonnes": None, "domestic_consumption_tonnes": None}
    )
    with pytest.raises(MarketFlowError, match=r"Mixed marketing years.*2024.*2025"):
        generate_monthly_flows(
            [missing, missing.model_copy(update={"marketing_year": "2025"})],
            month=date(2025, 1, 1),
            config=MonthlyFlowConfig(),
        )


@pytest.mark.parametrize(
    "month", [date(2025, 1, 2), datetime(2025, 1, 1), "2025-01-01", None, 0]
)
def test_invalid_month_is_a_domain_error(month: object) -> None:
    """Require an explicit first-of-month date even when no records are supplied."""
    with pytest.raises(MarketFlowError, match="first day of a month"):
        generate_monthly_flows([], month=cast(date, month), config=MonthlyFlowConfig())


@pytest.mark.parametrize("field", ["supply_weights", "consumption_weights"])
@pytest.mark.parametrize(
    "weights",
    [
        (),
        (1 / 11,) * 11,
        (1 / 13,) * 13,
        (-0.1, 1.1, *([0.0] * 10)),
        (float("nan"), *([0.0] * 11)),
        (float("inf"), *([0.0] * 11)),
        (float("-inf"), *([0.0] * 11)),
        (True, *([0.0] * 11)),
        ("1", *([0.0] * 11)),
        (0.05,) * 12,
    ],
)
def test_invalid_weights_are_rejected(field: str, weights: tuple[object, ...]) -> None:
    """Require twelve finite nonnegative numeric weights summing to one."""
    with pytest.raises(ValidationError, match=field):
        MonthlyFlowConfig.model_validate({field: weights})


def test_config_defaults_immutability_and_serialization() -> None:
    """Keep uniform allocation settings validated, immutable, and serializable."""
    config = MonthlyFlowConfig()
    assert config.supply_weights == config.consumption_weights == (1 / 12,) * 12
    assert MonthlyFlowConfig.model_validate_json(config.model_dump_json()) == config
    with pytest.raises(ValidationError, match="frozen_instance"):
        config.supply_weights = (1.0, *([0.0] * 11))
    with pytest.raises(ValidationError, match="extra_forbidden"):
        MonthlyFlowConfig.model_validate({"harvest_region": "brazil"})


def test_rounding_scale_weight_residual_is_allowed() -> None:
    """Accept harmless floating-point residuals without normalizing supplied weights."""
    weights = (1 + 5e-10, *([0.0] * 11))
    assert MonthlyFlowConfig(supply_weights=weights).supply_weights == weights


@pytest.mark.parametrize("field", ["production_tonnes", "domestic_consumption_tonnes"])
@pytest.mark.parametrize("quantity", [-1, float("nan"), float("inf"), float("-inf")])
def test_invalid_annual_quantities_are_rejected_even_at_zero_weight(
    balance: MarketBalance, field: str, quantity: float
) -> None:
    """Revalidate unchecked balance copies before multiplication could hide bad data."""
    invalid = balance.model_copy(
        update={
            "commodity": Commodity.SOYBEAN_MEAL,
            "crush_tonnes": None,
            field: quantity,
        }
    )
    config = MonthlyFlowConfig(
        supply_weights=(0.0, 1.0, *([0.0] * 10)),
        consumption_weights=(0.0, 1.0, *([0.0] * 10)),
    )
    with pytest.raises(MarketFlowError, match=field):
        generate_monthly_flows([invalid], month=date(2025, 1, 1), config=config)


def test_nonfinite_generated_quantity_is_rejected(balance: MarketBalance) -> None:
    """Reject overflow even when annual quantities and near-unit weights are finite."""
    large = balance.model_copy(update={"production_tonnes": float_info.max})
    config = MonthlyFlowConfig(supply_weights=(1 + 5e-10, *([0.0] * 11)))
    with pytest.raises(
        MarketFlowError, match=r"Invalid monthly supply.*brazil/soybeans"
    ):
        generate_monthly_flows([large], month=date(2025, 1, 1), config=config)


def test_unchecked_config_copy_is_revalidated(balance: MarketBalance) -> None:
    """Translate invalid allocation settings into the flow-generation error contract."""
    config = MonthlyFlowConfig().model_copy(update={"supply_weights": (0.1,) * 12})
    with pytest.raises(MarketFlowError, match="Invalid monthly flow configuration"):
        generate_monthly_flows([balance], month=date(2025, 1, 1), config=config)


def test_wrong_config_type_is_rejected() -> None:
    """Report a domain error for a caller that supplies no allocation configuration."""
    with pytest.raises(MarketFlowError, match="config must be a MonthlyFlowConfig"):
        generate_monthly_flows(
            [], month=date(2025, 1, 1), config=cast(MonthlyFlowConfig, None)
        )


def test_wrong_balance_type_is_rejected() -> None:
    """Require validated model records rather than implicitly interpreting mappings."""
    with pytest.raises(MarketFlowError, match="balances must contain MarketBalance"):
        generate_monthly_flows(
            [cast(MarketBalance, {})],
            month=date(2025, 1, 1),
            config=MonthlyFlowConfig(),
        )


def test_empty_balances_return_no_flows() -> None:
    """Keep empty selections empty rather than inventing annual quantities."""
    assert (
        generate_monthly_flows(
            iter(()), month=date(2025, 1, 1), config=MonthlyFlowConfig()
        )
        == ()
    )
