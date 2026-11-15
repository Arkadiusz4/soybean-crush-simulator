import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from soybean_sim import scenarios
from soybean_sim.agents import (
    Agent,
    AgentDecision,
    AgentIdentity,
    AgentType,
    Producer,
    ProducerConfig,
)
from soybean_sim.models import Commodity, MarketSnapshot, Region
from soybean_sim.scenarios import (
    ArgentinaDroughtConfig,
    MetricComparison,
    ScenarioComparison,
    ScenarioError,
    compare_simulation_results,
    run_argentina_drought_comparison,
)


def test_paired_execution_and_exact_recorded_metrics(
    comparison_inputs: dict[str, Any],
) -> None:
    """Measure actual crushing and trade shortfalls without counting intentions.

    Args:
        comparison_inputs (dict[str, Any]): Two-month real-agent market arguments.

    Returns:
        None: Confirms every requested metric with independent numeric expectations.
    """
    result = run_argentina_drought_comparison(**comparison_inputs)
    first, second = result.monthly_metrics()
    assert result.baseline.initial_state == result.drought.initial_state
    assert [first.month, second.month] == [date(2025, 1, 1), date(2025, 2, 1)]
    crushing = dict(first.crushing_tonnes)
    assert crushing[Region.ARGENTINA] == MetricComparison(baseline=80, drought=70)
    assert crushing[Region.BRAZIL].difference == 0
    assert first.trade_volume_tonnes == MetricComparison(baseline=20, drought=0)
    assert (first.baseline_trade_count, first.drought_trade_count) == (1, 0)
    markets = {(item.region, item.commodity): item for item in first.markets}
    arg = markets[Region.ARGENTINA, Commodity.SOYBEANS]
    brazil = markets[Region.BRAZIL, Commodity.SOYBEANS]
    assert arg.exports_tonnes == MetricComparison(baseline=20, drought=0)
    assert arg.imports_tonnes == MetricComparison(baseline=0, drought=0)
    assert arg.net_trade_tonnes == MetricComparison(baseline=-20, drought=0)
    assert brazil.imports_tonnes == MetricComparison(baseline=20, drought=0)
    assert brazil.exports_tonnes.difference == 0
    assert brazil.net_trade_tonnes == brazil.imports_tonnes
    assert brazil.inventory_tonnes == MetricComparison(baseline=20, drought=0)
    assert (
        arg.supply_tonnes
        == arg.demand_tonnes
        == MetricComparison(baseline=100, drought=70)
    )
    assert markets[
        Region.ARGENTINA, Commodity.SOYBEAN_MEAL
    ].supply_tonnes == MetricComparison(baseline=64, drought=56)
    assert markets[
        Region.ARGENTINA, Commodity.SOYBEAN_OIL
    ].inventory_tonnes == MetricComparison(baseline=16, drought=14)
    assert all(item.price_usd_per_tonne.difference == 0 for item in first.markets)
    later_price = next(
        item.price_usd_per_tonne
        for item in second.markets
        if (item.region, item.commodity) == (Region.BRAZIL, Commodity.SOYBEANS)
    )
    assert later_price.baseline == 200
    assert later_price.drought == 250
    assert later_price.difference == 50
    assert later_price.percentage_difference == 25
    assert all(
        item.demand_tonnes.baseline == item.demand_tonnes.drought == 0
        for item in first.markets
        if item.commodity != Commodity.SOYBEANS
    )


def test_generator_inputs_fresh_agents_and_baseline_immutability(
    comparison_inputs: dict[str, Any],
) -> None:
    """Materialize one-shot inputs once and never reuse either run's agent objects.

    Args:
        comparison_inputs (dict[str, Any]): Shared validated initial conditions.

    Returns:
        None: Confirms independent agents, exact supply-only transformation, and purity.
    """
    original_factory = comparison_inputs["agent_factory"]
    batches: list[tuple[Agent, ...]] = []

    def factory() -> tuple[Agent, ...]:
        """Record a fresh concrete batch.

        Returns:
            tuple[Agent, ...]: Fresh producer, crusher, and trader.
        """
        agents = original_factory()
        batches.append(agents)
        return agents

    state = comparison_inputs["initial_state"]
    flows = comparison_inputs["baseline_flows"]
    prices = comparison_inputs["regional_prices"]
    fingerprint = (
        state.model_dump_json(),
        tuple(flow.model_dump_json() for flow in flows),
        tuple(price.model_dump_json() for price in prices),
        comparison_inputs["drought_config"].model_dump_json(),
        comparison_inputs["price_dynamics_config"].model_dump_json(),
        tuple(
            config.model_dump_json()
            for config in comparison_inputs["crusher_configs"].values()
        ),
    )
    result = run_argentina_drought_comparison(
        **{
            **comparison_inputs,
            "agent_factory": factory,
            "baseline_flows": iter(flows),
            "regional_prices": iter(prices),
        }
    )
    assert len(batches) == 2
    assert not {id(agent) for agent in batches[0]} & {id(agent) for agent in batches[1]}
    for base_month, dry_month in zip(
        result.baseline.monthly_results, result.drought.monthly_results, strict=True
    ):
        assert base_month.market_flows[0].quantity_tonnes == 100
        assert dry_month.market_flows[0].quantity_tonnes == 70
    assert result == run_argentina_drought_comparison(**comparison_inputs)
    assert fingerprint == (
        state.model_dump_json(),
        tuple(flow.model_dump_json() for flow in flows),
        tuple(price.model_dump_json() for price in prices),
        comparison_inputs["drought_config"].model_dump_json(),
        comparison_inputs["price_dynamics_config"].model_dump_json(),
        tuple(
            config.model_dump_json()
            for config in comparison_inputs["crusher_configs"].values()
        ),
    )


@pytest.mark.parametrize("months", [1, 2])
def test_zero_reduction_and_single_month(
    comparison_inputs: dict[str, Any], months: int
) -> None:
    """Keep zero-loss scenarios equivalent, with no same-month price look-ahead.

    Args:
        comparison_inputs (dict[str, Any]): Common initial market.
        months (int): One-month or multi-month execution.

    Returns:
        None: Confirms matching histories, exact zeros, and expected result counts.
    """
    result = run_argentina_drought_comparison(
        **{
            **comparison_inputs,
            "months": months,
            "baseline_flows": comparison_inputs["baseline_flows"][:months],
            "drought_config": ArgentinaDroughtConfig(production_reduction_fraction=0),
        }
    )
    assert len(result.monthly_metrics()) == months
    assert result.baseline == result.drought
    for monthly in result.monthly_metrics():
        assert all(
            item.price_usd_per_tonne.difference
            == item.price_usd_per_tonne.percentage_difference
            == 0
            for item in monthly.markets
        )


def test_frozen_models_and_public_exports(comparison_inputs: dict[str, Any]) -> None:
    """Keep histories/metrics immutable and JSON-compatible through package exports.

    Args:
        comparison_inputs (dict[str, Any]): Valid comparison arguments.

    Returns:
        None: Confirms stable API names, immutable nested values, and round trips.
    """
    result = run_argentina_drought_comparison(**comparison_inputs)
    assert {
        "ScenarioComparison",
        "MetricComparison",
        "MonthlyComparison",
        "RegionalMarketComparison",
        "compare_simulation_results",
        "run_argentina_drought_comparison",
    } <= set(scenarios.__all__)
    assert ScenarioComparison.model_validate_json(result.model_dump_json()) == result
    monthly = result.monthly_metrics()[0]
    assert type(monthly).model_validate_json(monthly.model_dump_json()) == monthly
    with pytest.raises(ValidationError, match="frozen_instance"):
        result.drought_config = ArgentinaDroughtConfig()
    with pytest.raises(ValidationError, match="frozen_instance"):
        monthly.markets[0].inventory_tonnes.baseline = 5


@pytest.mark.parametrize(
    "defect",
    [
        "months",
        "initial",
        "legacy",
        "prices",
        "pressures",
        "inventories",
        "flow",
        "nonfinite",
        "initial_regional",
    ],
)
def test_incompatible_histories_are_rejected(
    comparison_inputs: dict[str, Any], defect: str
) -> None:
    """Reject missing observations rather than fabricating zero-valued metrics.

    Args:
        comparison_inputs (dict[str, Any]): Valid paired-run arguments.
        defect (str): Malformation injected through unsafe model copying.

    Returns:
        None: Confirms descriptive scenario-domain validation at the public boundary.
    """
    result = run_argentina_drought_comparison(**comparison_inputs)
    drought = result.drought
    first, second = drought.monthly_results
    if defect == "months":
        drought = drought.model_copy(
            update={"monthly_results": (first,), "final_state": first.ending_state}
        )
    elif defect == "initial":
        different = comparison_inputs["initial_state"].snapshot
        changed_stock = different.inventories[0].model_copy(
            update={"quantity_tonnes": 1}
        )
        state = comparison_inputs["initial_state"].model_copy(
            update={
                "snapshot": different.model_copy(
                    update={"inventories": (changed_stock, *different.inventories[1:])}
                )
            }
        )
        changed_first = first.model_copy(update={"starting_state": state})
        drought = drought.model_copy(
            update={"initial_state": state, "monthly_results": (changed_first, second)}
        )
    else:
        changes: dict[str, Any] = {}
        if defect == "legacy":
            # No trade decisions in this constructed record: isolate the mode check.
            changes = {
                "physical_cycle": False,
                "decisions": (),
                "settlement_results": (),
                "market_flows": (),
                "market_pressures": (),
                "executed_trades": (),
            }
        elif defect == "prices":
            changes = {"regional_prices": first.regional_prices[:-1]}
        elif defect == "pressures":
            changes = {"market_pressures": first.market_pressures[:-1]}
        elif defect == "inventories":
            snapshot = second.ending_state.snapshot.model_copy(
                update={"inventories": second.ending_state.snapshot.inventories[:-1]}
            )
            ending = second.ending_state.model_copy(update={"snapshot": snapshot})
            second = second.model_copy(update={"ending_state": ending})
            drought = drought.model_copy(
                update={"monthly_results": (first, second), "final_state": ending}
            )
        elif defect == "flow":
            changes = {
                "market_flows": (
                    first.market_flows[0].model_copy(update={"quantity_tonnes": 71}),
                )
            }
        elif defect == "nonfinite":
            changes = {
                "regional_prices": (
                    first.regional_prices[0].model_copy(
                        update={"price_usd_per_tonne": math.nan}
                    ),
                    *first.regional_prices[1:],
                )
            }
        elif defect == "initial_regional":
            changes = {
                "regional_prices": (
                    first.regional_prices[0].model_copy(
                        update={"price_usd_per_tonne": 999}
                    ),
                    *first.regional_prices[1:],
                )
            }
        if changes:
            drought = drought.model_copy(
                update={"monthly_results": (first.model_copy(update=changes), second)}
            )
    with pytest.raises(ScenarioError, match="Incompatible scenario comparison"):
        compare_simulation_results(
            result.baseline, drought, drought_config=result.drought_config
        )


def test_reused_agents_and_different_strategies_are_rejected(
    comparison_inputs: dict[str, Any],
) -> None:
    """Require a fresh factory and identical concrete configuration for comparability.

    Args:
        comparison_inputs (dict[str, Any]): Concrete agent strategy setup.

    Returns:
        None: Confirms identity, freshness, and strategy mismatches fail before runs.
    """
    agents = comparison_inputs["agent_factory"]()
    with pytest.raises(ScenarioError, match="fresh agent instances"):
        run_argentina_drought_comparison(
            **{**comparison_inputs, "agent_factory": lambda: agents}
        )
    first = Producer(
        AgentIdentity(
            agent_id="a", agent_type=AgentType.PRODUCER, region=Region.ARGENTINA
        ),
        ProducerConfig(reference_price_usd_per_tonne=250),
    )
    second = Producer(first.identity, ProducerConfig(reference_price_usd_per_tonne=300))
    batches = iter(((first,), (second,)))
    with pytest.raises(ScenarioError, match="strategy configurations differ"):
        run_argentina_drought_comparison(
            **{**comparison_inputs, "agent_factory": lambda: next(batches)}
        )


@pytest.mark.parametrize(
    ("baseline", "drought"),
    [(math.nan, 1), (1, math.inf), (True, 1), ("1", 2), (-1e308, 1e308)],
)
def test_metric_validation_rejects_nonfinite_or_overflowing_values(
    baseline: Any, drought: Any
) -> None:
    """Keep derived differences finite rather than returning misleading infinities.

    Args:
        baseline (Any): Candidate scalar baseline value.
        drought (Any): Candidate drought scalar value.

    Returns:
        None: Confirms strict metric validation and overflow protection.
    """
    with pytest.raises(ValidationError):
        MetricComparison(baseline=baseline, drought=drought)
    assert MetricComparison(baseline=0, drought=0).difference == 0
    assert MetricComparison(baseline=0, drought=5).percentage_difference is None


@dataclass
class _StatefulObserver:
    """Track calls to prove protocol-agent state stays isolated between runs."""

    identity: AgentIdentity
    months: list[date] = field(default_factory=list)

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Record one observation and abstain.

        Args:
            snapshot (MarketSnapshot): Current observed month.

        Returns:
            tuple[AgentDecision, ...]: Empty decisions, with no market mutation.
        """
        self.months.append(snapshot.month)
        return ()


def test_protocol_agent_mutable_state_is_independent(
    comparison_inputs: dict[str, Any],
) -> None:
    """Support custom Agent implementations without copying already-used instances.

    Args:
        comparison_inputs (dict[str, Any]): Initial conditions and monthly flows.

    Returns:
        None: Confirms exactly one call per month in separate mutable logs.
    """
    agents: list[_StatefulObserver] = []

    def create_observer() -> tuple[_StatefulObserver, ...]:
        """Create a fresh observer and retain its independent log.

        Returns:
            tuple[_StatefulObserver, ...]: One protocol-compatible agent.
        """
        observer = _StatefulObserver(
            AgentIdentity(
                agent_id="observer",
                agent_type=AgentType.TRADER,
                region=Region.ARGENTINA,
            )
        )
        agents.append(observer)
        return (observer,)

    run_argentina_drought_comparison(
        **{**comparison_inputs, "agent_factory": create_observer}
    )
    assert len(agents) == 2
    assert agents[0].months == agents[1].months == [date(2025, 1, 1), date(2025, 2, 1)]
    assert agents[0].months is not agents[1].months


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("agent_factory", None, "must be callable"),
        ("agent_factory", lambda: None, "return an iterable"),
        ("agent_factory", lambda: [object()], "return agents"),
        ("baseline_flows", None, "must be iterable"),
        ("regional_prices", 42, "must be iterable"),
        ("price_dynamics_config", None, "PriceDynamicsConfig"),
    ],
)
def test_invalid_paired_execution_inputs_are_clear(
    comparison_inputs: dict[str, Any],
    field_name: str,
    value: Any,
    message: str,
) -> None:
    """Reject malformed orchestration arguments without attempting scenario execution.

    Args:
        comparison_inputs (dict[str, Any]): Valid paired-run setup.
        field_name (str): Argument to invalidate.
        value (Any): Malformed argument value.
        message (str): Expected domain-error context.

    Returns:
        None: Confirms descriptive validation rather than opaque Python errors.
    """
    with pytest.raises(ScenarioError, match=message):
        run_argentina_drought_comparison(**{**comparison_inputs, field_name: value})
