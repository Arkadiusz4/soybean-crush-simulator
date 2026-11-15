"""Compare a synthetic January-December 2025 baseline with an Argentina drought."""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

import scenario_comparison_report as report
from pydantic import ValidationError
from rich.console import Console
from smoke_test_market import check_accounting

from soybean_sim import market
from soybean_sim.agents import (
    Agent,
    AgentIdentity,
    AgentType,
    Crusher,
    CrusherConfig,
    Producer,
    ProducerConfig,
    Trader,
    TraderConfig,
)
from soybean_sim.market import (
    MarketFlow,
    MarketState,
    MonthlyFlowConfig,
    PriceDynamicsConfig,
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
    ScenarioComparison,
    ScenarioError,
    apply_argentina_drought,
    run_argentina_drought_comparison,
)
from soybean_sim.simulation import SimulationError

_MONTHS: Final = tuple(date(2025, month, 1) for month in range(1, 13))


@dataclass(frozen=True)
class ComparisonDemo:
    """Keep synthetic common inputs; each run constructs its own agents."""

    state: MarketState
    flows: tuple[MarketFlow, ...]
    crusher_config: CrusherConfig
    price_config: PriceDynamicsConfig
    drought_config: ArgentinaDroughtConfig


def create_demo() -> ComparisonDemo:
    """Build four represented regions with buffers sufficient for twelve months.

    Returns:
        ComparisonDemo: Explicitly synthetic tonne/USD inputs, never rescaled datasets.
    """
    state = MarketState.from_snapshot(
        MarketSnapshot(
            month=_MONTHS[0],
            prices=tuple(
                CommodityPrice(
                    commodity=commodity,
                    month=_MONTHS[0],
                    price_usd_per_tonne=price,
                    data_source="synthetic scenario comparison",
                )
                for commodity, price in (
                    (Commodity.SOYBEANS, 250),
                    (Commodity.SOYBEAN_MEAL, 300),
                    (Commodity.SOYBEAN_OIL, 800),
                )
            ),
            inventories=tuple(
                Inventory(region=region, commodity=commodity, quantity_tonnes=1000)
                for region in Region
                for commodity in Commodity
            ),
        )
    )
    return ComparisonDemo(
        state,
        _create_flows(),
        CrusherConfig(monthly_capacity_tonnes=100, processing_cost_usd_per_tonne=10),
        PriceDynamicsConfig(),
        ArgentinaDroughtConfig(),
    )


def _create_flows() -> tuple[MarketFlow, ...]:
    """Allocate small synthetic annual figures using the existing uniform weights.

    Returns:
        tuple[MarketFlow, ...]: Twelve months of supply and buffered consumption.
    """
    balances = tuple(
        MarketBalance(
            region=region,
            commodity=commodity,
            marketing_year="synthetic",
            production_tonnes=production,
            domestic_consumption_tonnes=consumption,
            imports_tonnes=None,
            exports_tonnes=None,
            beginning_stocks_tonnes=None,
            ending_stocks_tonnes=None,
        )
        for region, commodity, production, consumption in (
            (Region.ARGENTINA, Commodity.SOYBEANS, 2400, None),
            (Region.BRAZIL, Commodity.SOYBEANS, 1200, None),
            (Region.UNITED_STATES, Commodity.SOYBEANS, 1200, None),
            (Region.ARGENTINA, Commodity.SOYBEAN_MEAL, 10800, 480),
            (Region.ARGENTINA, Commodity.SOYBEAN_OIL, 12000, 120),
            (Region.EUROPEAN_UNION, Commodity.SOYBEAN_MEAL, None, 240),
            (Region.EUROPEAN_UNION, Commodity.SOYBEAN_OIL, None, 120),
        )
    )
    return tuple(
        flow
        for month in _MONTHS
        for flow in generate_monthly_flows(
            balances,
            month=month,
            config=MonthlyFlowConfig(),
        )
    )


def create_agents(config: CrusherConfig) -> tuple[Agent, ...]:
    """Create real strategies; Argentina's offers get the matcher's regional priority.

    Args:
        config (CrusherConfig): Immutable Argentina processing settings.

    Returns:
        tuple[Agent, ...]: Three producers, an Argentina crusher, and an EU trader.
    """
    producers = tuple(
        Producer(
            AgentIdentity(
                agent_id=f"producer-{region.value}",
                agent_type=AgentType.PRODUCER,
                region=region,
            ),
            ProducerConfig(reference_price_usd_per_tonne=250),
        )
        for region in (Region.ARGENTINA, Region.BRAZIL, Region.UNITED_STATES)
    )
    crusher = Crusher(
        AgentIdentity(
            agent_id="crusher-ar", agent_type=AgentType.CRUSHER, region=Region.ARGENTINA
        ),
        config,
    )
    trader = Trader(
        AgentIdentity(
            agent_id="trader-eu",
            agent_type=AgentType.TRADER,
            region=Region.EUROPEAN_UNION,
        ),
        TraderConfig(
            reference_soybean_price_usd_per_tonne=250,
            reference_soybean_meal_price_usd_per_tonne=300,
            reference_soybean_oil_price_usd_per_tonne=800,
            maximum_monthly_purchase_tonnes=50,
        ),
    )
    return (*producers, crusher, trader)


def run_demo(case: ComparisonDemo) -> ScenarioComparison:
    """Execute both runs through the public comparison API with fresh concrete agents.

    Args:
        case (ComparisonDemo): Shared synthetic state, flows, and immutable settings.

    Returns:
        ScenarioComparison: Controlled baseline and drought histories for 2025.
    """
    return run_argentina_drought_comparison(
        case.state,
        case.flows,
        drought_config=case.drought_config,
        months=len(_MONTHS),
        agent_factory=lambda: create_agents(case.crusher_config),
        crusher_configs={"crusher-ar": case.crusher_config},
        price_dynamics_config=case.price_config,
    )


def fingerprint_inputs(case: ComparisonDemo) -> tuple[str, ...]:
    """Snapshot caller-owned inputs while excluding freshly created agent instances.

    Args:
        case (ComparisonDemo): Synthetic inputs to preserve across paired executions.

    Returns:
        tuple[str, ...]: Original ordered model representations.
    """
    return (
        case.state.model_dump_json(),
        case.crusher_config.model_dump_json(),
        case.price_config.model_dump_json(),
        case.drought_config.model_dump_json(),
        *(flow.model_dump_json() for flow in case.flows),
    )


def validate_demo(case: ComparisonDemo, comparison: ScenarioComparison) -> list[str]:
    """Check completed paired histories without recalculating prices or settlement.

    Args:
        case (ComparisonDemo): Original common conditions.
        comparison (ScenarioComparison): Completed baseline and drought runs.

    Returns:
        list[str]: Successful named validations; assertions stop an invalid demo.
    """
    assert tuple(item.month for item in comparison.baseline.monthly_results) == _MONTHS
    assert tuple(item.month for item in comparison.drought.monthly_results) == _MONTHS
    assert (
        comparison.baseline.initial_state
        == comparison.drought.initial_state
        == case.state
    )
    adjusted = apply_argentina_drought(case.flows, config=case.drought_config)
    assert (
        tuple(
            flow
            for month in comparison.drought.monthly_results
            for flow in month.market_flows
        )
        == adjusted
    )
    configs = {"crusher-ar": case.crusher_config}
    check_accounting(comparison.baseline, configs)
    check_accounting(comparison.drought, configs)
    metrics = comparison.monthly_metrics()
    assert all(len(month.markets) == len(Region) * len(Commodity) for month in metrics)
    assert any(
        item.inventory_tonnes.difference != 0
        or item.price_usd_per_tonne.difference != 0
        for month in metrics
        for item in month.markets
    ), "No downstream response"
    return [
        "Matching simulation periods",
        "Same initial conditions",
        "Argentina production reduction",
        "Physical accounting validity",
        "Complete regional price/pressure records",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the controlled comparison and print a concise or verbose Rich report.

    Args:
        argv (Sequence[str] | None): Script arguments; None reads the command line.

    Returns:
        int: Zero for valid results, one for a failed domain check or assertion.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose", action="store_true", help="Show monthly comparison trajectories."
    )
    options = parser.parse_args(argv)
    completed: list[str] = []
    stage = "Scenario setup and execution"
    try:
        case = create_demo()
        original = fingerprint_inputs(case)
        comparison = run_demo(case)
        stage = "Paired period, conditions, supply, and physical accounting"
        completed = validate_demo(case, comparison)
        stage = "Deterministic repeated execution"
        assert comparison == run_demo(case), "Repeated paired execution differs"
        completed.append(stage)
        stage = "Unchanged baseline data"
        assert fingerprint_inputs(case) == original, (
            "Caller-owned baseline inputs changed"
        )
        completed.append(stage)
    except (
        AssertionError,
        ScenarioError,
        SimulationError,
        ValidationError,
        market.FlowSettlementError,
        market.MarketFlowError,
        market.MarketPressureError,
        market.PriceDynamicsError,
        market.RegionalPriceError,
        market.SettlementError,
        market.TradeMatchingError,
        market.TradeSettlementError,
    ) as exc:
        report.print_failure(Console(stderr=True), completed, stage, exc)
        return 1
    report.print_report(
        Console(), comparison, completed=completed, verbose=options.verbose
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
