import math
import re
from dataclasses import replace
from types import ModuleType
from unittest.mock import Mock

import pytest

from soybean_sim.market import MarketFlowType
from soybean_sim.models import Commodity, Region
from soybean_sim.scenarios import ArgentinaDroughtConfig, ScenarioError


def test_twelve_month_drought_propagates_through_real_engine(
    scenario_demo: ModuleType,
) -> None:
    """Measure a supply shock with real agents, settlement, trade, and dynamic prices.

    Args:
        scenario_demo (ModuleType): Shared dataset-free twelve-month demonstration.

    Returns:
        None: Confirms controlled initial conditions and independently expected totals.
    """
    case = scenario_demo.create_demo()
    original = scenario_demo.fingerprint_inputs(case)
    comparison = scenario_demo.run_demo(case)
    assert len(scenario_demo.validate_demo(case, comparison)) == 5
    metrics = comparison.monthly_metrics()
    assert len(metrics) == 12
    assert {item.region for item in metrics[0].markets} == set(Region)
    production = [
        math.fsum(
            flow.quantity_tonnes
            for month in result.monthly_results
            for flow in month.market_flows
            if (flow.region, flow.commodity, flow.flow_type)
            == (Region.ARGENTINA, Commodity.SOYBEANS, MarketFlowType.SUPPLY)
        )
        for result in (comparison.baseline, comparison.drought)
    ]
    assert production == pytest.approx([2400, 1680])
    assert (
        math.fsum(
            pair.baseline for month in metrics for _, pair in month.crushing_tonnes
        )
        == 960
    )
    assert (
        math.fsum(
            pair.drought for month in metrics for _, pair in month.crushing_tonnes
        )
        == 850
    )
    assert math.fsum(month.trade_volume_tonnes.baseline for month in metrics) == 575
    assert math.fsum(month.trade_volume_tonnes.drought for month in metrics) == 575
    argentina = next(
        item
        for item in metrics[-1].markets
        if (item.region, item.commodity) == (Region.ARGENTINA, Commodity.SOYBEANS)
    )
    assert argentina.inventory_tonnes.baseline == 1865
    assert argentina.inventory_tonnes.drought == 1255
    assert argentina.price_usd_per_tonne.difference > 0
    assert all(
        item.price_usd_per_tonne.difference == 0
        for month in metrics
        for item in month.markets
        if item.region != Region.ARGENTINA
    )
    assert comparison == scenario_demo.run_demo(case)
    assert scenario_demo.fingerprint_inputs(case) == original


@pytest.mark.parametrize("verbose", [False, True])
def test_scenario_report_modes(
    scenario_demo: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    verbose: bool,
) -> None:
    """Render true observed values and execution totals without dumping raw histories.

    Args:
        scenario_demo (ModuleType): Standalone demo entry point.
        monkeypatch (pytest.MonkeyPatch): Stable terminal width.
        capsys (pytest.CaptureFixture[str]): Captured report streams.
        verbose (bool): Whether monthly detail is requested.

    Returns:
        None: Confirms labels, honest zero differences, trajectories, and PASS status.
    """
    monkeypatch.setenv("COLUMNS", "120")
    assert scenario_demo.main(["--verbose"] if verbose else []) == 0
    captured = capsys.readouterr()
    text = captured.out
    for label in (
        "Baseline vs Argentina Drought",
        "Synthetic data",
        "2025-01 to 2025-12",
        "Drought reduction: 30%",
        "Scenario Overview",
        "Regional Price Comparison",
        "observed 2025-12",
        "Physical Market Comparison",
        "Selected Ending Inventories",
        "Validation Summary",
        "Overall: PASS",
    ):
        assert label in text
    assert re.search(
        r"Soybean supply\s+2,400\.00\s+1,680\.00\s+-720\.00\s+-30\.00%", text
    )
    assert re.search(r"Executed crushing \(t\)\s+960\.00\s+850\.00\s+-110\.00", text)
    assert "Trade volume is unchanged" in text
    assert (
        "Monthly soybeans prices" in text
        if verbose
        else "Monthly soybeans prices" not in text
    )
    if verbose:
        for label in (
            "Monthly soybean meal prices",
            "Monthly soybean oil prices",
            "Monthly crushing",
            "Monthly executed trade volume",
            "Monthly selected soybean inventories",
        ):
            assert label in text
    assert "ScenarioComparison(" not in text
    assert captured.err == ""


def test_zero_loss_demonstration_inputs_produce_equal_results(
    scenario_demo: ModuleType,
) -> None:
    """Separate the transformation's zero-loss control from the default demo shock.

    Args:
        scenario_demo (ModuleType): Synthetic paired-run setup.

    Returns:
        None: Confirms a twelve-month zero-loss control is exactly equivalent.
    """
    case = replace(
        scenario_demo.create_demo(),
        drought_config=ArgentinaDroughtConfig(production_reduction_fraction=0),
    )
    comparison = scenario_demo.run_demo(case)
    assert comparison.baseline == comparison.drought


def test_scenario_script_returns_nonzero_on_domain_failure(
    scenario_demo: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report an execution failure without a misleading completed comparison.

    Args:
        scenario_demo (ModuleType): Standalone script entry point.
        monkeypatch (pytest.MonkeyPatch): Inject a domain failure.
        capsys (pytest.CaptureFixture[str]): Captured output.

    Returns:
        None: Confirms nonzero status, readable stage context, and no overall PASS.
    """
    monkeypatch.setattr(
        scenario_demo,
        "run_demo",
        Mock(side_effect=ScenarioError("Invalid comparison inputs")),
    )
    assert scenario_demo.main([]) == 1
    output = capsys.readouterr()
    assert "Invalid comparison inputs" in output.err
    assert "Scenario setup and execution" in output.err
    assert "Overall: FAIL" in output.err
    assert "Overall: PASS" not in output.out + output.err
