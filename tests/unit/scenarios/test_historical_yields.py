import math
from datetime import date
from typing import Any

import pytest

from soybean_sim.agents import CrusherConfig, TraderConfig
from soybean_sim.market import MarketFlowType, PriceDynamicsConfig
from soybean_sim.models import Commodity, CommodityPrice, MarketBalance, Region
from soybean_sim.scenarios import (
    ArgentinaDroughtConfig,
    HistoricalMarketError,
    HistoricalMarketInputs,
    calibrate_historical_crushers,
    compare_historical_market,
    preflight_historical_market,
    prepare_historical_market,
    run_historical_market,
)


@pytest.fixture
def prepared() -> HistoricalMarketInputs:
    """Prepare synthetic annual observations with a known four-percent residual.

    Returns:
        HistoricalMarketInputs: Four regions with explicit small boundary transfers.
    """
    production = {
        Commodity.SOYBEANS: 2400,
        Commodity.SOYBEAN_MEAL: 760,
        Commodity.SOYBEAN_OIL: 200,
    }
    balances = tuple(
        MarketBalance(
            region=region,
            commodity=commodity,
            marketing_year="2024",
            production_tonnes=production[commodity],
            domestic_consumption_tonnes=0 if commodity == Commodity.SOYBEANS else 12,
            imports_tonnes=12,
            exports_tonnes=6,
            beginning_stocks_tonnes=100,
            ending_stocks_tonnes=None,
            crush_tonnes=1000 if commodity == Commodity.SOYBEANS else None,
        )
        for region in Region
        for commodity in Commodity
    )
    prices = tuple(
        CommodityPrice(
            commodity=commodity,
            month=date(2025, 1, 1),
            price_usd_per_tonne=value,
            data_source="synthetic yield fixture",
        )
        for commodity, value in (
            (Commodity.SOYBEANS, 100),
            (Commodity.SOYBEAN_MEAL, 200),
            (Commodity.SOYBEAN_OIL, 300),
        )
    )
    return prepare_historical_market(
        balances,
        prices,
        marketing_year="2024",
        price_month=date(2025, 1, 1),
        start_month=date(2025, 1, 1),
        months=12,
        include_historical_trade=True,
    )


def settings(
    prepared: HistoricalMarketInputs, *, historical: bool = True
) -> dict[str, Any]:
    """Use unchanged strategies with explicit synthetic price and cost assumptions.

    Args:
        prepared (HistoricalMarketInputs): Annual production/input references.
        historical (bool): Derive raw product yields or keep synthetic defaults.

    Returns:
        dict: Common baseline/drought processing, trading, and price settings.
    """
    return dict(
        crusher_configs=calibrate_historical_crushers(
            prepared,
            processing_cost_usd_per_tonne=0,
            calibrate_yields=historical,
        ),
        trader_configs={
            region: TraderConfig(
                reference_soybean_price_usd_per_tonne=100,
                reference_soybean_meal_price_usd_per_tonne=200,
                reference_soybean_oil_price_usd_per_tonne=300,
                maximum_monthly_purchase_tonnes=0,
            )
            for region in Region
        },
        price_dynamics_config=PriceDynamicsConfig(),
    )


def test_historical_ratios_are_not_normalized(prepared: HistoricalMarketInputs) -> None:
    """Retain observed product ratios and explicitly journal their input remainder."""
    original = prepared.model_dump_json()
    configs = settings(prepared)["crusher_configs"]
    for config in configs.values():
        assert config.meal_yield_fraction == 0.76
        assert config.oil_yield_fraction == 0.20
        assert config.residual_yield_fraction == 0.04
        assert config.meal_yield_fraction + config.oil_yield_fraction < 1
    assert (
        CrusherConfig(
            monthly_capacity_tonnes=1, processing_cost_usd_per_tonne=0
        ).residual_yield_fraction
        == 0
    )
    assert prepared.model_dump_json() == original


@pytest.mark.parametrize(
    ("meal", "oil"),
    [
        (None, 200),
        (760, None),
        (900, 200),
        (0, 0),
        (-1, 200),
        (float("nan"), 200),
        (760, float("inf")),
        (1e308, 1e308),
    ],
)
def test_invalid_historical_yields_block_without_normalization(
    prepared: HistoricalMarketInputs,
    meal: float | None,
    oil: float | None,
) -> None:
    """Reject missing, nonfinite, or mass-creating product observations."""
    changed = prepared.model_copy(
        update={
            "balances": tuple(
                item.model_copy(
                    update={
                        "production_tonnes": meal
                        if item.commodity == Commodity.SOYBEAN_MEAL
                        else oil
                    }
                )
                if item.region == Region.BRAZIL and item.commodity != Commodity.SOYBEANS
                else item
                for item in prepared.balances
            )
        }
    )
    with pytest.raises(HistoricalMarketError):
        settings(changed)


def test_brazil_oil_shortfall_is_conversion_error_not_rounding(
    prepared: HistoricalMarketInputs,
) -> None:
    """Reproduce supplied Brazil 2024 observations with synthetic data elsewhere."""
    brazil = {
        Commodity.SOYBEANS: dict(
            production_tonnes=172500000,
            crush_tonnes=58162000,
            domestic_consumption_tonnes=62162000,
            imports_tonnes=731000,
            exports_tonnes=103142000,
            beginning_stocks_tonnes=29761000,
            ending_stocks_tonnes=37688000,
        ),
        Commodity.SOYBEAN_MEAL: dict(
            production_tonnes=44383000,
            domestic_consumption_tonnes=20500000,
            imports_tonnes=5000,
            exports_tonnes=23390000,
            beginning_stocks_tonnes=2972000,
            ending_stocks_tonnes=3470000,
        ),
        Commodity.SOYBEAN_OIL: dict(
            production_tonnes=11842000,
            domestic_consumption_tonnes=10410000,
            imports_tonnes=85000,
            exports_tonnes=1487000,
            beginning_stocks_tonnes=177000,
            ending_stocks_tonnes=207000,
        ),
    }
    balances = tuple(
        item.model_copy(update=brazil[item.commodity])
        if item.region == Region.BRAZIL
        else item
        for item in prepared.balances
    )
    case = prepare_historical_market(
        balances,
        prepared.reference_prices,
        marketing_year="2024",
        price_month=date(2025, 1, 1),
        start_month=date(2025, 1, 1),
        months=12,
        include_historical_trade=True,
    )
    before = case.model_dump_json()
    old = preflight_historical_market(case, **settings(case, historical=False))
    assert not old.can_run
    assert "shortfall 2,600.00 t" in " ".join(old.blocking_errors)
    calibrated = settings(case)
    config = calibrated["crusher_configs"][Region.BRAZIL]
    assert config.meal_yield_fraction == 44383000 / 58162000
    assert config.oil_yield_fraction == 11842000 / 58162000
    assert config.residual_yield_fraction == 1937000 / 58162000
    assert 177000 + 85000 + 58162000 * 0.2 - 10410000 - 1487000 == -2600
    assert (
        177000 + 85000 + 58162000 * config.oil_yield_fraction - 10410000 - 1487000
        == pytest.approx(207000)
    )
    assert preflight_historical_market(case, **calibrated).can_run
    assert case.model_dump_json() == before


def test_residual_aware_baseline_and_drought_accounting_are_reproducible(
    prepared: HistoricalMarketInputs,
) -> None:
    """Conserve inventory, boundary trade, consumption, and processing residual."""
    original = prepared.model_dump_json()
    config = settings(prepared)
    baseline = run_historical_market(prepared, **config)
    comparison = compare_historical_market(
        prepared, **config, drought_config=ArgentinaDroughtConfig()
    )
    assert baseline == comparison.baseline == run_historical_market(prepared, **config)
    assert comparison == compare_historical_market(
        prepared, **config, drought_config=ArgentinaDroughtConfig()
    )
    for history in (comparison.baseline, comparison.drought):
        assert len(history.monthly_results) == 12
        assert history.initial_state == prepared.state
        for month in history.monthly_results:
            stocks = month.ending_state.snapshot.inventories
            residual = math.fsum(
                item.residual_output_tonnes for item in month.settlement_results
            )
            consumption = math.fsum(
                flow.quantity_tonnes
                for flow in month.market_flows
                if flow.flow_type == MarketFlowType.CONSUMPTION
            )
            inputs = [
                item.quantity_tonnes
                for item in month.starting_state.snapshot.inventories
            ]
            inputs.extend(
                flow.quantity_tonnes
                for flow in month.market_flows
                if flow.commodity == Commodity.SOYBEANS
                and flow.flow_type == MarketFlowType.SUPPLY
            )
            inputs.extend(
                flow.quantity_tonnes if flow.is_import else -flow.quantity_tonnes
                for flow in month.external_trade_flows
            )
            assert math.fsum(inputs) == pytest.approx(
                math.fsum(item.quantity_tonnes for item in stocks)
                + residual
                + consumption
            )
            assert all(
                math.isfinite(item.quantity_tonnes) and item.quantity_tonnes >= 0
                for item in stocks
            )
            assert all(
                math.isfinite(item.price_usd_per_tonne) and item.price_usd_per_tonne > 0
                for item in month.regional_prices
            )
    assert any(
        item.residual_output_tonnes > 0
        for month in baseline.monthly_results
        for item in month.settlement_results
    )
    assert prepared.model_dump_json() == original
