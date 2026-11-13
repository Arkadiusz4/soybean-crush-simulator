import math
import random
from datetime import date, datetime
from sys import float_info
from typing import cast

import pytest
from pydantic import ValidationError

from soybean_sim import scenarios
from soybean_sim.market import (
    MarketFlow,
    MarketFlowType,
    MarketState,
    MonthlyFlowConfig,
    generate_monthly_flows,
)
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketBalance,
    MarketSnapshot,
    Region,
)
from soybean_sim.scenarios import (
    ArgentinaDroughtConfig,
    ScenarioError,
    apply_argentina_drought,
)
from soybean_sim.simulation import run_simulation


@pytest.fixture
def flow() -> MarketFlow:
    """Provide synthetic Argentine soybean supply across the year-boundary tests.

    Returns:
        MarketFlow: One thousand tonnes of December supply.
    """
    return MarketFlow(
        month=date(2024, 12, 1),
        region=Region.ARGENTINA,
        commodity=Commodity.SOYBEANS,
        flow_type=MarketFlowType.SUPPLY,
        quantity_tonnes=1000,
    )


@pytest.fixture
def mixed_flows(flow: MarketFlow) -> tuple[MarketFlow, ...]:
    """Cover every supported supply and product-consumption combination.

    Args:
        flow (MarketFlow): Common synthetic month and supply quantity.

    Returns:
        tuple[MarketFlow, ...]: Twenty distinct records across all four regions.
    """
    return tuple(
        flow.model_copy(
            update={
                "region": region,
                "commodity": commodity,
                "flow_type": flow_type,
                "quantity_tonnes": 1000 if flow_type == MarketFlowType.SUPPLY else 100,
            }
        )
        for region in Region
        for commodity in Commodity
        for flow_type in MarketFlowType
        if commodity != Commodity.SOYBEANS or flow_type == MarketFlowType.SUPPLY
    )


def test_public_api() -> None:
    """Expose the scenario's stable contracts at package level.

    Returns:
        None: Confirms public exports and the domain-error category.
    """
    assert {
        "ArgentinaDroughtConfig",
        "ScenarioError",
        "apply_argentina_drought",
    } <= set(scenarios.__all__)
    assert issubclass(ScenarioError, ValueError)


@pytest.mark.parametrize(
    ("reduction", "expected"),
    [
        (None, 700),
        (0.1, 900),
        (0.5, 500),
        (1.0, 0),
        (0.0, 1000),
    ],
)
def test_default_and_custom_reductions(
    flow: MarketFlow, reduction: float | None, expected: float
) -> None:
    """Apply the configured share while preserving all observation identifiers.

    Args:
        flow (MarketFlow): One thousand synthetic tonnes of Argentina supply.
        reduction (float | None): Custom share, or None to exercise the default.
        expected (float): Expected remaining supply in tonnes.

    Returns:
        None: Confirms the reduction and unchanged baseline record.
    """
    config = (
        ArgentinaDroughtConfig()
        if reduction is None
        else ArgentinaDroughtConfig(production_reduction_fraction=reduction)
    )
    (adjusted,) = apply_argentina_drought(iter([flow]), config=config)
    assert adjusted.quantity_tonnes == pytest.approx(expected)
    assert adjusted.model_dump(exclude={"quantity_tonnes"}) == flow.model_dump(
        exclude={"quantity_tonnes"}
    )
    assert adjusted is not flow
    assert flow.quantity_tonnes == 1000


def test_only_argentina_soybean_supply_changes(
    mixed_flows: tuple[MarketFlow, ...],
) -> None:
    """Preserve reference production, product consumption, and every other region.

    Args:
        mixed_flows (tuple[MarketFlow, ...]): All supported regional commodity flows.

    Returns:
        None: Confirms selective reduction without filtering or changing baseline data.
    """
    original = tuple(item.model_dump_json() for item in mixed_flows)
    adjusted = apply_argentina_drought(mixed_flows, config=ArgentinaDroughtConfig())
    assert len(adjusted) == len(mixed_flows)
    quantities = {
        (item.month, item.region, item.commodity, item.flow_type): item.quantity_tonnes
        for item in adjusted
    }
    for item in mixed_flows:
        expected = (
            700
            if (item.region, item.commodity, item.flow_type)
            == (Region.ARGENTINA, Commodity.SOYBEANS, MarketFlowType.SUPPLY)
            else item.quantity_tonnes
        )
        assert (
            quantities[item.month, item.region, item.commodity, item.flow_type]
            == expected
        )
    assert tuple(item.model_dump_json() for item in mixed_flows) == original


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (None, None, (700, 700, 700, 700)),
        (date(2024, 12, 1), date(2024, 12, 1), (1000, 700, 1000, 1000)),
        (date(2024, 12, 1), date(2025, 1, 1), (1000, 700, 700, 1000)),
        (None, date(2025, 1, 1), (700, 700, 700, 1000)),
        (date(2025, 1, 1), None, (1000, 1000, 700, 700)),
        (date(2026, 1, 1), date(2026, 2, 1), (1000, 1000, 1000, 1000)),
    ],
)
def test_inclusive_and_unbounded_intervals(
    flow: MarketFlow, start: date | None, end: date | None, expected: tuple[float, ...]
) -> None:
    """Select calendar months explicitly, including December-to-January intervals.

    Args:
        flow (MarketFlow): Common Argentina supply observation.
        start (date | None): Inclusive lower bound, possibly unbounded.
        end (date | None): Inclusive upper bound, possibly unbounded.
        expected (tuple[float, ...]): November-through-February quantities.

    Returns:
        None: Confirms boundary months are included and outside months unchanged.
    """
    months = (date(2024, 11, 1), date(2024, 12, 1), date(2025, 1, 1), date(2025, 2, 1))
    records = tuple(flow.model_copy(update={"month": month}) for month in months)
    result = apply_argentina_drought(
        reversed(records),
        config=ArgentinaDroughtConfig(start_month=start, end_month=end),
    )
    assert tuple(item.month for item in result) == months
    assert tuple(item.quantity_tonnes for item in result) == pytest.approx(expected)


@pytest.mark.parametrize("reduction", [0.0, 0.3, 1.0])
def test_zero_quantities_are_preserved(flow: MarketFlow, reduction: float) -> None:
    """Keep explicit zero supply as a record rather than treating it as missing.

    Args:
        flow (MarketFlow): Template observation.
        reduction (float): Valid loss fraction.

    Returns:
        None: Confirms zero remains present and valid.
    """
    zero = flow.model_copy(update={"quantity_tonnes": 0})
    assert apply_argentina_drought(
        [zero], config=ArgentinaDroughtConfig(production_reduction_fraction=reduction)
    ) == (zero,)


def test_missing_argentina_and_empty_inputs_are_allowed(
    mixed_flows: tuple[MarketFlow, ...],
) -> None:
    """Allow partial regional coverage and empty collections without inventing flows.

    Args:
        mixed_flows (tuple[MarketFlow, ...]): Complete synthetic collection.

    Returns:
        None: Confirms no minimum coverage or implicit zero records.
    """
    records = tuple(item for item in mixed_flows if item.region != Region.ARGENTINA)
    result = apply_argentina_drought(records, config=ArgentinaDroughtConfig())
    assert set(result) == set(records)
    assert apply_argentina_drought(iter(()), config=ArgentinaDroughtConfig()) == ()


@pytest.mark.parametrize("duplicate_quantity", [1000, 1500])
@pytest.mark.parametrize("region", [Region.ARGENTINA, Region.BRAZIL])
def test_duplicate_keys_are_rejected_even_outside_drought(
    flow: MarketFlow, duplicate_quantity: float, region: Region
) -> None:
    """Reject identical or conflicting duplicates before eligibility filtering.

    Args:
        flow (MarketFlow): Template supply observation.
        duplicate_quantity (float): Repeated or conflicting physical quantity.
        region (Region): Affected or unaffected region.

    Returns:
        None: Confirms every duplicate key is rejected even outside the date interval.
    """
    first = flow.model_copy(update={"region": region})
    duplicate = first.model_copy(update={"quantity_tonnes": duplicate_quantity})
    with pytest.raises(ScenarioError, match=r"Duplicate flow.*2024-12-01"):
        apply_argentina_drought(
            [first, duplicate],
            config=ArgentinaDroughtConfig(start_month=date(2026, 1, 1)),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("production_reduction_fraction", -0.1),
        ("production_reduction_fraction", 1.1),
        ("production_reduction_fraction", math.nan),
        ("production_reduction_fraction", math.inf),
        ("production_reduction_fraction", -math.inf),
        ("production_reduction_fraction", True),
        ("production_reduction_fraction", False),
        ("production_reduction_fraction", "0.3"),
        ("production_reduction_fraction", None),
        ("production_reduction_fraction", complex(0.3)),
        ("start_month", date(2024, 12, 2)),
        ("end_month", date(2024, 12, 31)),
        ("start_month", "2024-12-01"),
        ("end_month", datetime(2024, 12, 1)),
        ("start_month", True),
        ("end_month", 0),
    ],
)
def test_configuration_fields_are_strict_and_revalidated(
    flow: MarketFlow, field: str, value: object
) -> None:
    """Reject malformed values both during construction and after unsafe model copies.

    Args:
        flow (MarketFlow): Valid supply passed alongside invalid settings.
        field (str): Configuration field to invalidate.
        value (object): Value violating numeric or calendar constraints.

    Returns:
        None: Confirms Pydantic errors at construction and scenario errors at the API.
    """
    with pytest.raises(ValidationError):
        ArgentinaDroughtConfig.model_validate({field: value})
    invalid = ArgentinaDroughtConfig().model_copy(update={field: value})
    with pytest.raises(ScenarioError, match="Invalid drought configuration"):
        apply_argentina_drought([flow], config=invalid)


def test_configuration_interval_and_immutability(flow: MarketFlow) -> None:
    """Reject reversed intervals and keep valid settings frozen and serializable.

    Args:
        flow (MarketFlow): Valid supply passed to the scenario.

    Returns:
        None: Confirms temporal validation, immutability, unknown-field rejection,
            and JSON round trips.
    """
    config = ArgentinaDroughtConfig(
        start_month=date(2024, 12, 1), end_month=date(2025, 1, 1)
    )
    assert (
        ArgentinaDroughtConfig.model_validate_json(config.model_dump_json()) == config
    )
    with pytest.raises(ValidationError, match="frozen_instance"):
        config.production_reduction_fraction = 0.5
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ArgentinaDroughtConfig.model_validate({"severity": 0.5})
    invalid = {"start_month": date(2025, 1, 1), "end_month": date(2024, 12, 1)}
    with pytest.raises(ValidationError, match="must not be after"):
        ArgentinaDroughtConfig.model_validate(invalid)
    with pytest.raises(ScenarioError, match="must not be after"):
        apply_argentina_drought([flow], config=config.model_copy(update=invalid))


@pytest.mark.parametrize("config", [None, {}, 0, True, "drought"])
def test_configuration_must_be_the_correct_model(config: object) -> None:
    """Reject invalid configuration objects even when there are no observations.

    Args:
        config (object): Value outside the public configuration contract.

    Returns:
        None: Confirms configuration validation precedes empty-flow handling.
    """
    with pytest.raises(ScenarioError, match="config must be an ArgentinaDroughtConfig"):
        apply_argentina_drought((), config=cast(ArgentinaDroughtConfig, config))


@pytest.mark.parametrize("records", [None, 1, True, [None], [{}], ["flow"], "flow"])
def test_invalid_collection_or_record_types(records: object) -> None:
    """Reject malformed iterables and record types with scenario-specific context.

    Args:
        records (object): Invalid collection or observations.

    Returns:
        None: Confirms errors rather than silent filtering.
    """
    with pytest.raises(ScenarioError, match="MarketFlow"):
        apply_argentina_drought(
            cast(tuple[MarketFlow, ...], records), config=ArgentinaDroughtConfig()
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity_tonnes": -1},
        {"quantity_tonnes": math.nan},
        {"quantity_tonnes": math.inf},
        {"quantity_tonnes": -math.inf},
        {"quantity_tonnes": True},
        {"quantity_tonnes": "100"},
        {"month": date(2024, 12, 2)},
        {"month": "2024-12-01"},
        {"month": datetime(2024, 12, 1)},
        {"region": "unknown"},
        {"commodity": "corn"},
        {"flow_type": "export"},
    ],
)
def test_all_records_are_revalidated_before_returning(
    flow: MarketFlow, changes: dict[str, object]
) -> None:
    """Reject malformed records even when they cannot qualify for a reduction.

    Args:
        flow (MarketFlow): Valid first record.
        changes (dict[str, object]): Corruption applied to an unaffected second record.

    Returns:
        None: Confirms row context and complete validation outside the drought period.
    """
    invalid = flow.model_copy(update={"region": Region.BRAZIL, **changes})
    with pytest.raises(ScenarioError, match="Invalid flow 2"):
        apply_argentina_drought(
            [flow, invalid], config=ArgentinaDroughtConfig(start_month=date(2026, 1, 1))
        )
    assert flow.quantity_tonnes == 1000


@pytest.mark.parametrize("quantity", [0, 10])
def test_soybean_consumption_is_unsupported(flow: MarketFlow, quantity: float) -> None:
    """Keep the existing no-double-counting convention for soybean consumption.

    Args:
        flow (MarketFlow): Supply template to turn into unsupported consumption.
        quantity (float): Zero or positive unsupported quantity.

    Returns:
        None: Confirms unsupported consumption is rejected rather than reduced.
    """
    consumption = flow.model_copy(
        update={"flow_type": MarketFlowType.CONSUMPTION, "quantity_tonnes": quantity}
    )
    with pytest.raises(ScenarioError, match="unsupported soybean consumption"):
        apply_argentina_drought([consumption], config=ArgentinaDroughtConfig())


def test_shuffled_inputs_are_deterministic_and_baseline_is_isolated(
    mixed_flows: tuple[MarketFlow, ...],
) -> None:
    """Sort keys consistently across months without changing source data or settings.

    Args:
        mixed_flows (tuple[MarketFlow, ...]): All supported physical flow categories.

    Returns:
        None: Confirms stable ordering, independent repeated use, and immutable results.
    """
    baseline = [
        item.model_copy(update={"month": month})
        for month in (date(2025, 1, 1), date(2024, 12, 1))
        for item in mixed_flows
    ]
    config = ArgentinaDroughtConfig()
    original = ([item.model_dump_json() for item in baseline], config.model_dump_json())
    expected = apply_argentina_drought(baseline, config=config)
    keys = [
        (item.month, item.region.value, item.commodity.value, item.flow_type.value)
        for item in expected
    ]
    assert keys == sorted(keys)
    for seed in (0, 17, 42):
        shuffled = list(baseline)
        random.Random(seed).shuffle(shuffled)
        assert apply_argentina_drought(iter(shuffled), config=config) == expected
    assert (
        [item.model_dump_json() for item in baseline],
        config.model_dump_json(),
    ) == original
    assert isinstance(expected, tuple)
    with pytest.raises(ValidationError, match="frozen_instance"):
        expected[0].quantity_tonnes = 1
    assert MarketFlow.model_validate_json(expected[0].model_dump_json()) == expected[0]


@pytest.mark.parametrize("reduction", [0.0, 0.3, 1.0])
def test_largest_finite_supply_remains_finite(
    flow: MarketFlow, reduction: float
) -> None:
    """Preserve safe numerical bounds even at the largest supported finite quantity.

    Args:
        flow (MarketFlow): Synthetic supply template.
        reduction (float): Valid loss share including both extremes.

    Returns:
        None: Confirms validated adjusted quantities remain finite and nonnegative.
    """
    maximum = flow.model_copy(update={"quantity_tonnes": float_info.max})
    (result,) = apply_argentina_drought(
        [maximum],
        config=ArgentinaDroughtConfig(production_reduction_fraction=reduction),
    )
    assert math.isfinite(result.quantity_tonnes)
    assert 0 <= result.quantity_tonnes <= float_info.max
    assert result.quantity_tonnes == pytest.approx(float_info.max * (1 - reduction))


def test_generated_and_adjusted_flows_work_with_physical_engine(
    flow: MarketFlow,
) -> None:
    """Pass scenario inputs through existing generation and physical settlement.

    Args:
        flow (MarketFlow): Synthetic Argentina supply month and identifiers.

    Returns:
        None: Confirms engine compatibility with unchanged baseline flows and state.
    """
    balance = MarketBalance(
        region=Region.ARGENTINA,
        commodity=Commodity.SOYBEANS,
        marketing_year="synthetic",
        production_tonnes=12000,
        imports_tonnes=None,
        exports_tonnes=None,
        domestic_consumption_tonnes=None,
        beginning_stocks_tonnes=100,
        ending_stocks_tonnes=None,
    )
    baseline = generate_monthly_flows(
        [balance], month=flow.month, config=MonthlyFlowConfig()
    )
    adjusted = apply_argentina_drought(baseline, config=ArgentinaDroughtConfig())
    state = MarketState.from_snapshot(
        MarketSnapshot(
            month=flow.month,
            prices=tuple(
                CommodityPrice(
                    commodity=commodity,
                    month=flow.month,
                    price_usd_per_tonne=100,
                    data_source="synthetic scenario compatibility",
                )
                for commodity in Commodity
            ),
            inventories=tuple(
                Inventory(
                    region=Region.ARGENTINA,
                    commodity=commodity,
                    quantity_tonnes=100 if commodity == Commodity.SOYBEANS else 0,
                )
                for commodity in Commodity
            ),
        )
    )
    original = state.model_dump_json()
    result = run_simulation(
        state, [], crusher_configs={}, months=1, monthly_flows=adjusted
    )
    assert result.monthly_results[0].market_flows == adjusted
    assert (
        next(
            stock.quantity_tonnes
            for stock in result.final_state.snapshot.inventories
            if stock.commodity == Commodity.SOYBEANS
        )
        == 800
    )
    assert baseline[0].quantity_tonnes == 1000
    assert state.model_dump_json() == original
    assert result.final_state.snapshot.prices == state.snapshot.prices
