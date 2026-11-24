"""Compare a synthetic January-December 2025 baseline with an Argentina drought."""

import argparse
from collections.abc import Sequence
from datetime import date

from pydantic import ValidationError
from rich.console import Console

if __package__:
    from . import scenario_comparison_report as report
    from .smoke_test_market import check_accounting
else:
    import scenario_comparison_report as report
    from smoke_test_market import check_accounting

from dashboard.demo import ComparisonDemo, create_demo, run_demo
from soybean_sim import market
from soybean_sim.models import (
    Commodity,
    Region,
)
from soybean_sim.scenarios import (
    ScenarioComparison,
    ScenarioError,
    apply_argentina_drought,
)
from soybean_sim.simulation import SimulationError

_MONTHS = tuple(date(2025, month, 1) for month in range(1, 13))


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
