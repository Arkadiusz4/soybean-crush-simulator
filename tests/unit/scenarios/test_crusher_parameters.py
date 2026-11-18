from dataclasses import replace

import pytest
from pydantic import ValidationError

from scripts.compare_scenarios import create_agents, create_demo
from soybean_sim.agents import CrusherConfig
from soybean_sim.models import Commodity, Region
from soybean_sim.scenarios import (
    CrusherParameterShockConfig,
    apply_crusher_parameter_shock,
    run_production_shock_comparison,
)


@pytest.mark.parametrize("region", list(Region))
@pytest.mark.parametrize(
    ("capacity", "cost"), [(25, 20), (-50, -20), (0, 0), (0, -100)]
)
def test_transformation(region: Region, capacity: float, cost: float) -> None:
    """Change only selected parameters and return independent validated models."""
    baseline = {
        item: CrusherConfig(
            monthly_capacity_tonnes=100, processing_cost_usd_per_tonne=10
        )
        for item in Region
    }
    before = {key: value.model_dump() for key, value in baseline.items()}
    shock = CrusherParameterShockConfig(
        region=region,
        capacity_change_percent=capacity,
        processing_cost_change_percent=cost,
    )
    actual = apply_crusher_parameter_shock(baseline, config=shock)
    assert actual[region].monthly_capacity_tonnes == pytest.approx(
        100 * (1 + capacity / 100)
    )
    assert actual[region].processing_cost_usd_per_tonne == pytest.approx(
        10 * (1 + cost / 100)
    )
    for key in Region:
        assert actual[key].meal_yield_fraction == baseline[key].meal_yield_fraction
        assert actual[key].oil_yield_fraction == baseline[key].oil_yield_fraction
        assert actual[key] is not baseline[key]
        if key != region:
            assert actual[key] == baseline[key]
    assert before == {key: value.model_dump() for key, value in baseline.items()}
    assert actual == apply_crusher_parameter_shock(baseline, config=shock)


@pytest.mark.parametrize(
    "values",
    [
        dict(capacity_change_percent=-100),
        dict(capacity_change_percent=float("inf")),
        dict(processing_cost_change_percent=-101),
        dict(processing_cost_change_percent=float("nan")),
    ],
)
def test_invalid_values(values: dict) -> None:
    """Reject zero capacity and unsupported finite ranges."""
    with pytest.raises(ValidationError):
        CrusherParameterShockConfig(region=Region.BRAZIL, **values)


def test_missing_region() -> None:
    """Do not invent a Crusher in a region absent from the baseline."""
    with pytest.raises(ValueError, match="No Crusher"):
        apply_crusher_parameter_shock(
            {}, config=CrusherParameterShockConfig(region=Region.BRAZIL)
        )


def test_real_capacity_cost_and_limits() -> None:
    """Exercise capacity and profitability through the real engine."""
    case = create_demo()

    def run(capacity=0, cost=0, initial=case.state):
        return run_production_shock_comparison(
            initial,
            case.flows,
            shock_config=CrusherParameterShockConfig(
                region=Region.ARGENTINA,
                capacity_change_percent=capacity,
                processing_cost_change_percent=cost,
            ),
            months=12,
            agent_factory=lambda: create_agents(case.crusher_config),
            crusher_configs={"crusher-ar": case.crusher_config},
            price_dynamics_config=case.price_config,
        )

    increased = run(capacity=25)
    assert sum(
        m.crushing_tonnes[0][1].drought for m in increased.monthly_metrics()
    ) > sum(m.crushing_tonnes[0][1].baseline for m in increased.monthly_metrics())
    assert all(
        a.market_flows == b.market_flows
        for a, b in zip(
            increased.baseline.monthly_results,
            increased.drought.monthly_results,
            strict=True,
        )
    )
    assert increased.baseline.initial_state == increased.drought.initial_state
    before = {
        item.commodity: item.quantity_tonnes
        for item in increased.baseline.final_state.snapshot.inventories
        if item.region == Region.ARGENTINA
    }
    after = {
        item.commodity: item.quantity_tonnes
        for item in increased.drought.final_state.snapshot.inventories
        if item.region == Region.ARGENTINA
    }
    assert after.keys() == before.keys()
    assert after[Commodity.SOYBEAN_MEAL] > before[Commodity.SOYBEAN_MEAL]
    assert after[Commodity.SOYBEAN_OIL] > before[Commodity.SOYBEAN_OIL]
    expensive_case = replace(
        case,
        crusher_config=case.crusher_config.model_copy(
            update={"processing_cost_usd_per_tonne": 1000.0}
        ),
    )
    stopped = run_production_shock_comparison(
        expensive_case.state,
        expensive_case.flows,
        shock_config=CrusherParameterShockConfig(
            region=Region.ARGENTINA, processing_cost_change_percent=-100
        ),
        months=12,
        agent_factory=lambda: create_agents(expensive_case.crusher_config),
        crusher_configs={"crusher-ar": expensive_case.crusher_config},
        price_dynamics_config=case.price_config,
    )
    assert stopped.monthly_metrics()[0].crushing_tonnes[0][1].baseline == 0
    assert stopped.monthly_metrics()[0].crushing_tonnes[0][1].drought > 0
    assert sum(m.crushing_tonnes[0][1].drought for m in stopped.monthly_metrics()) > 0


def test_capacity_has_no_effect_without_soybeans() -> None:
    """An empty regional stock prevents processing at either capacity."""
    from soybean_sim.market import MarketState

    case = create_demo()
    snapshot = case.state.snapshot.model_copy(
        update={
            "inventories": tuple(
                item.model_copy(update={"quantity_tonnes": 0.0})
                if item.region == Region.ARGENTINA
                and item.commodity == Commodity.SOYBEANS
                else item
                for item in case.state.snapshot.inventories
            )
        }
    )
    comparison = run_production_shock_comparison(
        MarketState.from_snapshot(snapshot),
        (),
        months=1,
        shock_config=CrusherParameterShockConfig(
            region=Region.ARGENTINA, capacity_change_percent=100
        ),
        agent_factory=lambda: create_agents(case.crusher_config),
        crusher_configs={"crusher-ar": case.crusher_config},
        price_dynamics_config=case.price_config,
    )
    metric = comparison.monthly_metrics()[0].crushing_tonnes[0][1]
    assert metric.baseline == metric.drought == 0


def test_comparison_requires_exact_parameter_change() -> None:
    """Reject an unrelated regional configuration change in imported metadata."""
    case = create_demo()
    comparison = run_production_shock_comparison(
        case.state,
        case.flows,
        months=12,
        shock_config=CrusherParameterShockConfig(
            region=Region.ARGENTINA, capacity_change_percent=25
        ),
        agent_factory=lambda: create_agents(case.crusher_config),
        crusher_configs={"crusher-ar": case.crusher_config},
        price_dynamics_config=case.price_config,
    )
    assert type(comparison).model_validate(comparison.model_dump()) == comparison
    invalid = {Region.ARGENTINA: case.crusher_config}
    with pytest.raises(ValidationError, match="configurations do not match"):
        type(comparison)(
            baseline=comparison.baseline,
            drought=comparison.drought,
            drought_config=comparison.drought_config,
            baseline_crusher_configs=comparison.baseline_crusher_configs,
            modified_crusher_configs=invalid,
        )
