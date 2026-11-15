from datetime import date
from typing import Any

import pytest

from soybean_sim.agents import CrusherConfig, TraderConfig
from soybean_sim.market import MarketFlowType, MonthlyFlowConfig, PriceDynamicsConfig
from soybean_sim.models import Commodity, CommodityPrice, MarketBalance, Region
from soybean_sim.scenarios import (
    ArgentinaDroughtConfig,
    HistoricalMarketError,
    HistoricalMarketInputs,
    apply_argentina_drought,
    calibrate_historical_crushers,
    compare_historical_market,
    preflight_historical_market,
    prepare_historical_market,
    run_historical_market,
)


@pytest.fixture
def balances() -> tuple[MarketBalance, ...]:
    """Provide explicitly synthetic annual data and independent marketing-year stocks.

    Returns:
        tuple[MarketBalance, ...]: Complete observations for all supported markets.
    """
    return tuple(
        MarketBalance(
            region=region,
            commodity=commodity,
            marketing_year="2024",
            production_tonnes=1200,
            imports_tonnes=None,
            exports_tonnes=None,
            domestic_consumption_tonnes=24
            if commodity == Commodity.SOYBEAN_MEAL
            else 12,
            beginning_stocks_tonnes=100,
            ending_stocks_tonnes=None,
        )
        for region in Region
        for commodity in Commodity
    )


@pytest.fixture
def prices() -> tuple[CommodityPrice, ...]:
    """Provide one synthetic reference observation per commodity.

    Returns:
        tuple[CommodityPrice, ...]: January observations without regional prices.
    """
    return tuple(
        CommodityPrice(
            commodity=commodity,
            month=date(2025, 1, 1),
            price_usd_per_tonne=value,
            data_source="synthetic historical integration fixture",
        )
        for commodity, value in (
            (Commodity.SOYBEANS, 100),
            (Commodity.SOYBEAN_MEAL, 200),
            (Commodity.SOYBEAN_OIL, 300),
        )
    )


@pytest.fixture
def prepared(
    balances: tuple[MarketBalance, ...], prices: tuple[CommodityPrice, ...]
) -> HistoricalMarketInputs:
    """Prepare a feasible synthetic twelve-month historical-format selection.

    Args:
        balances (tuple[MarketBalance, ...]): Complete annual observations.
        prices (tuple[CommodityPrice, ...]): Selected global references.

    Returns:
        HistoricalMarketInputs: Unscaled stocks and generated monthly allocations.
    """
    return prepare_historical_market(
        balances,
        prices,
        marketing_year="2024",
        price_month=date(2025, 1, 1),
        start_month=date(2025, 1, 1),
        months=12,
    )


@pytest.fixture
def settings() -> dict[str, Any]:
    """Set capacities independently of annual historical figures.

    Returns:
        dict[str, Any]: Small-scale settings used only for synthetic tests.
    """
    return dict(
        crusher_configs={
            region: CrusherConfig(
                monthly_capacity_tonnes=10, processing_cost_usd_per_tonne=35
            )
            for region in Region
        },
        trader_configs={
            region: TraderConfig(
                reference_soybean_price_usd_per_tonne=100,
                reference_soybean_meal_price_usd_per_tonne=200,
                reference_soybean_oil_price_usd_per_tonne=300,
                maximum_monthly_purchase_tonnes=5,
            )
            for region in Region
        },
        price_dynamics_config=PriceDynamicsConfig(),
    )


def test_exact_preparation_alignment_and_no_scaling(
    prepared: HistoricalMarketInputs,
) -> None:
    """Keep annual quantities, source dates, and original stock units explicit.

    Args:
        prepared (HistoricalMarketInputs): Synthetic prepared records.

    Returns:
        None: Confirms full coverage, exact allocation, and unadjusted stocks.
    """
    assert len(prepared.balances) == 12 and len(prepared.reference_prices) == 3
    assert len(prepared.flows) == 240
    assert prepared.metadata.marketing_year == "2024"
    assert (
        prepared.metadata.reference_price_month
        == prepared.state.snapshot.month
        == date(2025, 1, 1)
    )
    assert prepared.metadata.months[-1] == date(2025, 12, 1)
    assert all(
        stock.quantity_tonnes == 100 for stock in prepared.state.snapshot.inventories
    )
    assert all(balance.production_tonnes == 1200 for balance in prepared.balances)
    assert all(
        flow.quantity_tonnes == 100
        for flow in prepared.flows
        if flow.flow_type == MarketFlowType.SUPPLY
    )
    assert all(
        flow.commodity != Commodity.SOYBEANS
        for flow in prepared.flows
        if flow.flow_type == MarketFlowType.CONSUMPTION
    )
    assert "marketing-year-start" in " ".join(prepared.metadata.assumptions)


@pytest.mark.parametrize(
    "defect",
    [
        "missing_balance",
        "missing_price",
        "production",
        "reference_production",
        "consumption",
        "stocks",
        "negative",
        "nonfinite",
        "year",
        "price_period",
        "start_period",
    ],
)
def test_missing_and_invalid_source_observations_block_preparation(
    balances: tuple[MarketBalance, ...], prices: tuple[CommodityPrice, ...], defect: str
) -> None:
    """Reject missing data and incompatible periods rather than substituting values.

    Args:
        balances (tuple[MarketBalance, ...]): Valid annual data.
        prices (tuple[CommodityPrice, ...]): Valid global references.
        defect (str): Source field or selection to invalidate.

    Returns:
        None: Confirms inspectable blocking diagnostics with no prepared result.
    """
    arguments: dict[str, Any] = dict(
        marketing_year="2024",
        price_month=date(2025, 1, 1),
        start_month=date(2025, 1, 1),
        months=12,
    )
    if defect == "missing_balance":
        balances = balances[:-1]
    elif defect == "missing_price":
        prices = prices[:-1]
    elif defect in (
        "production",
        "reference_production",
        "consumption",
        "stocks",
        "negative",
        "nonfinite",
    ):
        commodity = (
            Commodity.SOYBEAN_MEAL
            if defect in ("reference_production", "consumption")
            else Commodity.SOYBEANS
        )
        field_name = (
            "domestic_consumption_tonnes"
            if defect == "consumption"
            else "beginning_stocks_tonnes"
            if defect == "stocks"
            else "production_tonnes"
        )
        value = (
            -1
            if defect == "negative"
            else float("nan")
            if defect == "nonfinite"
            else None
        )
        changed = False
        records = []
        for balance in balances:
            if balance.commodity == commodity and not changed:
                balance = balance.model_copy(update={field_name: value})
                changed = True
            records.append(balance)
        balances = tuple(records)
    elif defect == "year":
        arguments["marketing_year"] = "2030"
    elif defect == "price_period":
        arguments["price_month"] = arguments["start_month"] = date(2025, 2, 1)
    else:
        arguments["start_month"] = date(2025, 2, 1)
    with pytest.raises(HistoricalMarketError) as caught:
        prepare_historical_market(balances, prices, **arguments)
    assert not caught.value.diagnostics.can_run
    assert caught.value.diagnostics.blocking_errors


@pytest.mark.parametrize(
    "defect",
    [
        "flow_missing",
        "flow_duplicate",
        "flow_quantity",
        "flow_date",
        "inventory",
        "price",
        "annual_duplicate",
        "negative_inventory",
        "empty_months",
    ],
)
def test_preflight_revalidates_and_protects_source_quantities(
    prepared: HistoricalMarketInputs, settings: dict[str, Any], defect: str
) -> None:
    """Block copied/corrupted inputs including artificial stocks or altered consumption.

    Args:
        prepared (HistoricalMarketInputs): Unchanged valid source preparation.
        settings (dict[str, Any]): Explicit agent and price rules.
        defect (str): Prepared record or source relationship to invalidate.

    Returns:
        None: Confirms complete validation without repairing the mutated copy.
    """
    original = prepared.model_dump_json()
    if defect.startswith("flow"):
        flows = prepared.flows
        if defect == "flow_missing":
            flows = flows[:-1]
        elif defect == "flow_duplicate":
            flows = (*flows, flows[0])
        else:
            update = (
                {"quantity_tonnes": 1}
                if defect == "flow_quantity"
                else {"month": date(2030, 1, 1)}
            )
            flows = (flows[0].model_copy(update=update), *flows[1:])
        corrupted = prepared.model_copy(update={"flows": flows})
    elif defect in ("inventory", "negative_inventory", "price"):
        snapshot = prepared.state.snapshot
        if defect == "price":
            changes = {
                "prices": (
                    snapshot.prices[0].model_copy(update={"price_usd_per_tonne": 1}),
                    *snapshot.prices[1:],
                )
            }
        else:
            changes = {
                "inventories": (
                    snapshot.inventories[0].model_copy(
                        update={
                            "quantity_tonnes": -1
                            if defect == "negative_inventory"
                            else 1000
                        }
                    ),
                    *snapshot.inventories[1:],
                )
            }
        corrupted = prepared.model_copy(
            update={
                "state": prepared.state.model_copy(
                    update={"snapshot": snapshot.model_copy(update=changes)}
                )
            }
        )
    elif defect == "annual_duplicate":
        corrupted = prepared.model_copy(
            update={"balances": (*prepared.balances, prepared.balances[0])}
        )
    else:
        corrupted = prepared.model_copy(
            update={"metadata": prepared.metadata.model_copy(update={"months": ()})}
        )
    assert not preflight_historical_market(corrupted, **settings).can_run
    assert prepared.model_dump_json() == original


def test_capacity_bound_blocks_without_crediting_reference_production(
    balances: tuple[MarketBalance, ...],
    prices: tuple[CommodityPrice, ...],
    settings: dict[str, Any],
) -> None:
    """Block a local product deficit even though annual reference production is large.

    Args:
        balances (tuple[MarketBalance, ...]): Complete source-style data.
        prices (tuple[CommodityPrice, ...]): Reference prices.
        settings (dict[str, Any]): Ten-tonne processing capacity per month.

    Returns:
        None: Confirms month, region, commodity, demand, and shortage diagnostics.
    """
    records = tuple(
        item.model_copy(
            update={
                "beginning_stocks_tonnes": 0,
                "production_tonnes": 12000000,
                "domestic_consumption_tonnes": 1200,
            }
        )
        if (item.region, item.commodity) == (Region.BRAZIL, Commodity.SOYBEAN_OIL)
        else item
        for item in balances
    )
    prepared = prepare_historical_market(
        records,
        prices,
        marketing_year="2024",
        price_month=date(2025, 1, 1),
        start_month=date(2025, 1, 1),
        months=12,
    )
    checked = preflight_historical_market(prepared, **settings)
    assert not checked.can_run
    message = " ".join(checked.blocking_errors)
    for phrase in (
        "brazil/soybean_oil",
        "2025-01-01",
        "100.00",
        "2.00",
        "98.00",
        "reference production",
    ):
        assert phrase in message
    result = run_historical_market(prepared, **settings)
    oil = next(
        item
        for item in result.monthly_results[0].consumption_results
        if item.region == Region.BRAZIL and item.commodity == Commodity.SOYBEAN_OIL
    )
    assert oil.requested_tonnes == 100
    assert oil.unmet_tonnes > 0
    assert oil.fulfilled_tonnes <= 2


@pytest.mark.parametrize("paired", [False, True])
def test_agent_numeric_failure_retains_execution_context(
    balances: tuple[MarketBalance, ...],
    prices: tuple[CommodityPrice, ...],
    settings: dict[str, Any],
    paired: bool,
) -> None:
    """Translate known agent failures without returning incomplete histories.

    Args:
        balances (tuple[MarketBalance, ...]): Feasible stock/flow templates.
        prices (tuple[CommodityPrice, ...]): Finite reference-price templates.
        settings (dict[str, Any]): Explicit configurations to stress numerically.
        paired (bool): Exercise baseline or paired historical execution.

    Returns:
        None: Confirms a domain failure retains the active scenario and month.
    """
    references = tuple(
        item.model_copy(
            update={
                "price_usd_per_tonne": 1e308
                if item.commodity == Commodity.SOYBEANS
                else 1,
            }
        )
        for item in prices
    )
    prepared = prepare_historical_market(
        balances,
        references,
        marketing_year="2024",
        price_month=date(2025, 1, 1),
        start_month=date(2025, 1, 1),
        months=1,
    )
    settings["crusher_configs"] = {
        region: config.model_copy(update={"processing_cost_usd_per_tonne": 1e308})
        for region, config in settings["crusher_configs"].items()
    }
    assert preflight_historical_market(prepared, **settings).can_run
    with pytest.raises(
        HistoricalMarketError,
        match=r"last observed decisions: baseline 2025-01-01",
    ) as caught:
        if paired:
            compare_historical_market(
                prepared, **settings, drought_config=ArgentinaDroughtConfig()
            )
        else:
            run_historical_market(prepared, **settings)
    assert "margin" in str(caught.value).lower()


def test_budget_overflow_identifies_market_and_month(
    prepared: HistoricalMarketInputs, settings: dict[str, Any]
) -> None:
    """Report extreme-capacity arithmetic failures with market context.

    Args:
        prepared (HistoricalMarketInputs): Twelve-month synthetic inputs.
        settings (dict[str, Any]): Settings to give an impractically large capacity.

    Returns:
        None: Confirms a quantitative blocker instead of a raw arithmetic failure.
    """
    settings["crusher_configs"] = {
        region: config.model_copy(update={"monthly_capacity_tonnes": 1e308})
        for region, config in settings["crusher_configs"].items()
    }
    report = preflight_historical_market(prepared, **settings)
    assert not report.can_run
    message = " ".join(report.blocking_errors)
    assert "argentina/soybeans 2025-02-01" in message
    assert "overflow" in message
    assert "1e+308" in message


def test_calibration_records_shortage_after_clear_preflight(
    balances: tuple[MarketBalance, ...],
    prices: tuple[CommodityPrice, ...],
    settings: dict[str, Any],
) -> None:
    """Let margin-based abstention produce recorded unmet demand after screening.

    Args:
        balances (tuple[MarketBalance, ...]): Source records to remove product buffers.
        prices (tuple[CommodityPrice, ...]): Price templates.
        settings (dict[str, Any]): Capacity bound sufficient for demand in isolation.

    Returns:
        None: Confirms unmet demand is retained and stock stays nonnegative.
    """
    annual = tuple(
        item.model_copy(update={"beginning_stocks_tonnes": 0})
        if item.commodity != Commodity.SOYBEANS
        else item
        for item in balances
    )
    references = tuple(
        item.model_copy(
            update={
                "price_usd_per_tonne": 1000
                if item.commodity == Commodity.SOYBEANS
                else 1
            }
        )
        for item in prices
    )
    prepared = prepare_historical_market(
        annual,
        references,
        marketing_year="2024",
        price_month=date(2025, 1, 1),
        start_month=date(2025, 1, 1),
        months=1,
    )
    assert preflight_historical_market(prepared, **settings).can_run
    result = run_historical_market(prepared, **settings)
    meal = next(
        item
        for item in result.monthly_results[0].consumption_results
        if item.region == Region.ARGENTINA and item.commodity == Commodity.SOYBEAN_MEAL
    )
    assert (meal.requested_tonnes, meal.fulfilled_tonnes, meal.unmet_tonnes) == (
        2,
        0,
        2,
    )


def test_feasible_execution_and_drought_compatibility(
    prepared: HistoricalMarketInputs, settings: dict[str, Any]
) -> None:
    """Use the real physical engine and paired scenario API without changing inputs.

    Args:
        prepared (HistoricalMarketInputs): Feasible synthetic-format observations.
        settings (dict[str, Any]): Explicit, common historical-run assumptions.

    Returns:
        None: Confirms deterministic histories and supply-only drought.
    """
    original = prepared.model_dump_json()
    assert preflight_historical_market(prepared, **settings).can_run
    result = run_historical_market(prepared, **settings)
    assert len(result.monthly_results) == 12
    assert all(
        month.physical_cycle and month.regional_prices and month.market_pressures
        for month in result.monthly_results
    )
    assert result == run_historical_market(prepared, **settings)
    comparison = compare_historical_market(
        prepared, **settings, drought_config=ArgentinaDroughtConfig()
    )
    assert comparison.baseline == result
    assert (
        comparison.baseline.initial_state
        == comparison.drought.initial_state
        == prepared.state
    )
    assert tuple(
        flow
        for month in comparison.drought.monthly_results
        for flow in month.market_flows
    ) == apply_argentina_drought(prepared.flows, config=ArgentinaDroughtConfig())
    assert prepared.model_dump_json() == original


def test_drought_records_unmet_demand_after_a_feasible_baseline(
    balances: tuple[MarketBalance, ...],
    prices: tuple[CommodityPrice, ...],
    settings: dict[str, Any],
) -> None:
    """Preserve baseline success while recording a drought-specific shortage.

    Args:
        balances (tuple[MarketBalance, ...]): Source-style synthetic observations.
        prices (tuple[CommodityPrice, ...]): Profitable initial references.
        settings (dict[str, Any]): Common regional configuration templates.

    Returns:
        None: Confirms the paired result records drought-specific unmet demand.
    """
    records = tuple(
        item.model_copy(
            update={
                "beginning_stocks_tonnes": 0,
                "domestic_consumption_tonnes": 900
                if item.commodity == Commodity.SOYBEAN_MEAL
                else 228,
            }
        )
        for item in balances
    )
    settings = {
        **settings,
        "crusher_configs": {
            region: CrusherConfig(
                monthly_capacity_tonnes=100, processing_cost_usd_per_tonne=0
            )
            for region in Region
        },
        "trader_configs": {
            region: config.model_copy(update={"maximum_monthly_purchase_tonnes": 0})
            for region, config in settings["trader_configs"].items()
        },
    }
    prepared = prepare_historical_market(
        records,
        prices,
        marketing_year="2024",
        price_month=date(2025, 1, 1),
        start_month=date(2025, 1, 1),
        months=1,
    )
    assert run_historical_market(prepared, **settings).monthly_results
    comparison = compare_historical_market(
        prepared, **settings, drought_config=ArgentinaDroughtConfig()
    )
    baseline = comparison.baseline.monthly_results[0].consumption_results
    drought = comparison.drought.monthly_results[0].consumption_results
    assert sum(item.unmet_tonnes for item in baseline) == 0
    assert sum(item.unmet_tonnes for item in drought) > 0


def test_preparation_is_order_independent_and_supports_explicit_weights(
    balances: tuple[MarketBalance, ...],
    prices: tuple[CommodityPrice, ...],
    prepared: HistoricalMarketInputs,
) -> None:
    """Preserve determinism and the caller's documented monthly allocation assumptions.

    Args:
        balances (tuple[MarketBalance, ...]): Original annual observations.
        prices (tuple[CommodityPrice, ...]): Original monthly references.
        prepared (HistoricalMarketInputs): Default allocation result.

    Returns:
        None: Confirms canonical order and custom weights without annual rescaling.
    """
    assert (
        prepare_historical_market(
            reversed(balances),
            reversed(prices),
            marketing_year="2024",
            price_month=date(2025, 1, 1),
            start_month=date(2025, 1, 1),
            months=12,
        )
        == prepared
    )
    custom = prepare_historical_market(
        balances,
        prices,
        marketing_year="2024",
        price_month=date(2025, 1, 1),
        start_month=date(2025, 1, 1),
        months=1,
        flow_config=MonthlyFlowConfig(supply_weights=(1, *([0.0] * 11))),
    )
    assert all(
        flow.quantity_tonnes == 1200
        for flow in custom.flows
        if flow.flow_type == MarketFlowType.SUPPLY
    )


@pytest.fixture
def throughput_prepared(prepared: HistoricalMarketInputs) -> HistoricalMarketInputs:
    """Attach synthetic observed crush volumes without changing stocks or flows.

    Args:
        prepared (HistoricalMarketInputs): Complete baseline records.

    Returns:
        HistoricalMarketInputs: Independent records with distinct regional throughput.
    """
    annual_crush = {
        region: 1200 * index for index, region in enumerate(sorted(Region), 1)
    }
    return prepared.model_copy(
        update={
            "balances": tuple(
                item.model_copy(update={"crush_tonnes": annual_crush[item.region]})
                if item.commodity == Commodity.SOYBEANS
                else item
                for item in prepared.balances
            ),
        }
    )


def test_historical_capacity_uses_observed_crush_without_utilization_compensation(
    throughput_prepared: HistoricalMarketInputs,
) -> None:
    """Scale throughput directly, retaining source observations and default decisions.

    Args:
        throughput_prepared (HistoricalMarketInputs): Distinct annual crush references.

    Returns:
        None: Confirms annual/12 capacity and unchanged economic heuristics.
    """
    before = throughput_prepared.model_dump_json()
    configs = calibrate_historical_crushers(
        throughput_prepared, processing_cost_usd_per_tonne=35
    )
    assert list(configs) == sorted(Region)
    for index, region in enumerate(sorted(Region), 1):
        assert configs[region] == CrusherConfig(
            monthly_capacity_tonnes=100 * index,
            processing_cost_usd_per_tonne=35,
        )
    assert configs == calibrate_historical_crushers(
        throughput_prepared, processing_cost_usd_per_tonne=35
    )
    assert throughput_prepared.model_dump_json() == before


@pytest.mark.parametrize("crush", [None, 0, -1, float("nan"), float("inf")])
def test_capacity_calibration_requires_positive_observed_crush(
    throughput_prepared: HistoricalMarketInputs,
    crush: float | None,
) -> None:
    """Reject unsupported throughput instead of inventing positive capacity.

    Args:
        throughput_prepared (HistoricalMarketInputs): Complete annual references.
        crush (float | None): Missing or invalid throughput to inject.

    Returns:
        None: Confirms clear calibration diagnostics.
    """
    changed = throughput_prepared.model_copy(
        update={
            "balances": tuple(
                item.model_copy(update={"crush_tonnes": crush})
                if (item.region, item.commodity) == (Region.BRAZIL, Commodity.SOYBEANS)
                else item
                for item in throughput_prepared.balances
            ),
        }
    )
    with pytest.raises(HistoricalMarketError, match="calibration blocked"):
        calibrate_historical_crushers(changed, processing_cost_usd_per_tonne=35)


def test_capacity_is_annualized_independently_of_requested_horizon(
    throughput_prepared: HistoricalMarketInputs,
) -> None:
    """Never concentrate a whole year's processing into a shorter requested run.

    Args:
        throughput_prepared (HistoricalMarketInputs): Twelve-month reference data.

    Returns:
        None: Confirms unchanged capacity for a one-month simulation.
    """
    short = prepare_historical_market(
        throughput_prepared.balances,
        throughput_prepared.reference_prices,
        marketing_year="2024",
        price_month=date(2025, 1, 1),
        start_month=date(2025, 1, 1),
        months=1,
    )
    assert calibrate_historical_crushers(
        short, processing_cost_usd_per_tonne=35
    ) == calibrate_historical_crushers(
        throughput_prepared, processing_cost_usd_per_tonne=35
    )


@pytest.mark.parametrize("opening_meal", [0, 10])
def test_preflight_bounds_soybean_imports_and_their_processing_timing(
    balances: tuple[MarketBalance, ...],
    prices: tuple[CommodityPrice, ...],
    settings: dict[str, Any],
    opening_meal: float,
) -> None:
    """Distinguish physical purchase opportunities from unexecuted USDA imports.

    Args:
        balances (tuple[MarketBalance, ...]): Synthetic annual source templates.
        prices (tuple[CommodityPrice, ...]): Profitable reference prices.
        settings (dict[str, Any]): Explicit processing and trading rules.
        opening_meal (float): Buffer allowing or preventing January settlement.

    Returns:
        None: Confirms purchases can support only later months' crushing bounds.
    """
    changes = {
        Commodity.SOYBEANS: {"production_tonnes": 0, "beginning_stocks_tonnes": 0},
        Commodity.SOYBEAN_MEAL: {
            "domestic_consumption_tonnes": 120,
            "beginning_stocks_tonnes": opening_meal,
        },
        Commodity.SOYBEAN_OIL: {
            "domestic_consumption_tonnes": 0,
            "beginning_stocks_tonnes": 0,
        },
    }
    records = tuple(
        item.model_copy(update={**changes[item.commodity], "imports_tonnes": 999000})
        if item.region == Region.EUROPEAN_UNION
        else item
        for item in balances
    )
    prepared = prepare_historical_market(
        records,
        prices,
        marketing_year="2024",
        price_month=date(2025, 1, 1),
        start_month=date(2025, 1, 1),
        months=12,
    )
    settings["crusher_configs"] = {
        region: config.model_copy(update={"monthly_capacity_tonnes": 100})
        for region, config in settings["crusher_configs"].items()
    }
    settings["trader_configs"] = {
        region: config.model_copy(update={"maximum_monthly_purchase_tonnes": 20})
        for region, config in settings["trader_configs"].items()
    }
    before = prepared.model_dump_json()
    report = preflight_historical_market(prepared, **settings)
    assert report.can_run == bool(opening_meal)
    if not opening_meal:
        message = " ".join(report.blocking_errors)
        assert "european_union/soybean_meal 2025-01-01" in message
        assert "Cumulative soybean input bound 0.00 t" in message
        assert "999000.0" in message
        assert "not an executed import" in message
    assert prepared.model_dump_json() == before
