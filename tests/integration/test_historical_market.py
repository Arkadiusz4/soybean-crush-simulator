from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

from soybean_sim.market import MarketFlowType
from soybean_sim.models import Commodity, Region
from soybean_sim.scenarios import (
    ArgentinaDroughtConfig,
    HistoricalMarketError,
    compare_historical_market,
    load_historical_market,
    preflight_historical_market,
    run_historical_market,
)


def test_local_loaders_to_physical_history(
    historical_paths: tuple[Path, Path], historical_script: ModuleType
) -> None:
    """Use actual parsers, selection, allocation, agents, settlement, and accounting."""
    prepared = load_historical_market(*historical_paths)
    assert prepared == load_historical_market(*historical_paths)
    assert len(prepared.balances) == 12
    assert len(prepared.reference_prices) == 3
    assert len(prepared.flows) == 240
    assert len(prepared.metadata.usda_sha256) == 64
    assert len(prepared.metadata.world_bank_sha256) == 64
    assert prepared.metadata.marketing_year == "2024"
    assert prepared.metadata.reference_price_month == date(2025, 1, 1)
    assert all(
        item.quantity_tonnes == 100 for item in prepared.state.snapshot.inventories
    )
    assert all(
        item.quantity_tonnes == 100
        for item in prepared.flows
        if item.flow_type == MarketFlowType.SUPPLY
        and item.commodity == Commodity.SOYBEANS
    )
    before = prepared.model_dump_json()
    crushers, traders, pricing = historical_script.create_settings(prepared)
    assert all(config.monthly_capacity_tonnes == 10 for config in crushers.values())
    result = run_historical_market(
        prepared,
        crusher_configs=crushers,
        trader_configs=traders,
        price_dynamics_config=pricing,
    )
    assert len(result.monthly_results) == 12
    historical_script.check_accounting(
        result,
        {f"crusher-{region.value}": config for region, config in crushers.items()},
    )
    assert prepared.model_dump_json() == before


@pytest.mark.parametrize("verbose", [False, True])
def test_historical_terminal_complete_runs(
    historical_paths: tuple[Path, Path],
    historical_script: ModuleType,
    capsys: pytest.CaptureFixture[str],
    verbose: bool,
) -> None:
    """Distinguish complete file-backed runs from preflight clearance."""
    arguments = [
        "--usda-path",
        str(historical_paths[0]),
        "--world-bank-path",
        str(historical_paths[1]),
        "--compare",
    ]
    if verbose:
        arguments.append("--verbose")
    assert historical_script.main(arguments) == 0
    output = capsys.readouterr().out
    assert "Data preparation: PASS" in output
    assert "Preflight: CLEAR" in output
    assert "Physical execution: PASS" in output
    assert "annual USDA soybean crush / 12" in output
    assert "Processing Feasibility" in output
    assert "Baseline/drought comparison: PASS" in output
    assert "Overall: PASS" in output
    assert ("USDA SHA-256" in output) == verbose
    assert ("Original USDA Marketing-Year" in output) == verbose


def test_historical_terminal_reports_unmet_demand(
    historical_paths: tuple[Path, Path],
    historical_script: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report unmet demand when source consumption exceeds physical availability."""
    path = historical_paths[0]
    content = path.read_text().replace(
        "125,Domestic Consumption,08,(1000 MT),0.012",
        "125,Domestic Consumption,08,(1000 MT),1200",
    )
    path.write_text(content)

    assert (
        historical_script.main(
            [
                "--usda-path",
                str(path),
                "--world-bank-path",
                str(historical_paths[1]),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Unmet" in output
    assert "Physical execution: PASS" in output
    assert "Overall: PASS" in output


def test_missing_local_source_is_explicit(
    historical_paths: tuple[Path, Path],
    historical_script: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Never replace a missing file with synthetic fallback observations."""
    missing = historical_paths[0].with_name("missing.csv")
    with pytest.raises(HistoricalMarketError, match=r"missing\.csv"):
        load_historical_market(missing, historical_paths[1])
    assert (
        historical_script.main(
            [
                "--usda-path",
                str(missing),
                "--world-bank-path",
                str(historical_paths[1]),
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "missing.csv" in output
    assert "Overall: BLOCKED" in output
    assert "Overall: PASS" not in output


def test_unavailable_source_period(historical_paths: tuple[Path, Path]) -> None:
    """Fail selection rather than substituting a nearby reference observation."""
    with pytest.raises(HistoricalMarketError, match="price"):
        load_historical_market(
            *historical_paths,
            price_month=date(2025, 2, 1),
            start_month=date(2025, 2, 1),
        )


def test_default_run_does_not_request_comparison(
    historical_paths: tuple[Path, Path],
    historical_script: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Make the optional comparison status clear in the concise terminal report."""
    assert (
        historical_script.main(
            [
                "--usda-path",
                str(historical_paths[0]),
                "--world-bank-path",
                str(historical_paths[1]),
            ]
        )
        == 0
    )
    assert "Baseline/drought comparison: NOT REQUESTED" in capsys.readouterr().out


_RAW = Path(__file__).resolve().parents[2] / "data/raw"
_USDA = _RAW / "psd_oilseeds.csv"
_WORLD_BANK = _RAW / "CMO-Historical-Data-Monthly.xlsx"


@pytest.mark.skipif(
    not (_USDA.is_file() and _WORLD_BANK.is_file()),
    reason="Optional local historical datasets are absent; no download attempted",
)
def test_optional_real_historical_preflight(historical_script: ModuleType) -> None:
    """Check real source coverage and feasibility when local files exist."""
    prepared = load_historical_market(_USDA, _WORLD_BANK, include_historical_trade=True)
    assert len(prepared.balances) == len(Region) * len(Commodity)
    assert len(prepared.reference_prices) == len(Commodity)
    assert len(prepared.flows) == 240
    assert prepared.metadata.usda_sha256
    assert prepared.metadata.world_bank_sha256
    crushers, traders, pricing = historical_script.create_settings(prepared)
    annual_crush = {
        item.region: item.crush_tonnes
        for item in prepared.balances
        if item.commodity == Commodity.SOYBEANS
    }
    assert all(
        config.monthly_capacity_tonnes == annual_crush[region] / 12
        for region, config in crushers.items()
    )
    settings = dict(
        crusher_configs=crushers, trader_configs=traders, price_dynamics_config=pricing
    )
    report = preflight_historical_market(prepared, **settings, calibration=True)
    assert report.metadata == prepared.metadata
    assert report.warnings
    assert report.can_run
    original = prepared.model_dump_json()
    result = run_historical_market(prepared, **settings)
    assert len(result.monthly_results) == 12
    assert result == run_historical_market(prepared, **settings)
    comparison = compare_historical_market(
        prepared, **settings, drought_config=ArgentinaDroughtConfig()
    )
    assert comparison.baseline == result
    assert all(not month.external_trade_flows for month in result.monthly_results)
    historical_script.check_accounting(
        result,
        {f"crusher-{region.value}": config for region, config in crushers.items()},
    )
    assert prepared.model_dump_json() == original


def test_historical_regional_production_shock(
    historical_paths: tuple[Path, Path], historical_script: ModuleType
) -> None:
    """Apply a Brazil supply increase through the calibrated historical engine."""
    from soybean_sim.scenarios import ProductionShockConfig

    prepared = load_historical_market(*historical_paths)
    crushers, traders, pricing = historical_script.create_settings(prepared)
    result = run_historical_market(
        prepared,
        crusher_configs=crushers,
        trader_configs=traders,
        price_dynamics_config=pricing,
        production_shock=ProductionShockConfig(
            region=Region.BRAZIL,
            percentage_change=20,
            start_month=date(2025, 3, 1),
            end_month=date(2025, 3, 1),
        ),
    )
    march = result.monthly_results[2]
    flow = next(
        item
        for item in march.market_flows
        if item.region == Region.BRAZIL
        and item.commodity == Commodity.SOYBEANS
        and item.flow_type == MarketFlowType.SUPPLY
    )
    assert flow.quantity_tonnes == pytest.approx(120)
    assert result.initial_state == prepared.state


def test_historical_drought_single_run(
    historical_paths: tuple[Path, Path],
    historical_script: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run one calibrated shock with the original state and partial flows."""
    from soybean_sim.scenarios import _historical_market

    prepared = load_historical_market(*historical_paths)
    crushers, traders, pricing = historical_script.create_settings(prepared)
    original = prepared.model_dump_json()
    run = _historical_market.run_simulation
    calls = []

    def recording_run(*args, **kwargs):
        calls.append(kwargs)
        return run(*args, **kwargs)

    monkeypatch.setattr(_historical_market, "run_simulation", recording_run)
    result = run_historical_market(
        prepared,
        crusher_configs=crushers,
        trader_configs=traders,
        price_dynamics_config=pricing,
        drought_config=ArgentinaDroughtConfig(
            production_reduction_fraction=0.3,
            start_month=date(2025, 3, 1),
            end_month=date(2025, 3, 1),
        ),
    )
    assert len(calls) == 1 and calls[0]["demand_aware_consumption"] is True
    assert result.initial_state == prepared.state and len(result.monthly_results) == 12
    for month in result.monthly_results:
        baseline = tuple(flow for flow in prepared.flows if flow.month == month.month)
        assert (set(month.market_flows) != set(baseline)) == (
            month.month == date(2025, 3, 1)
        )
    assert prepared.model_dump_json() == original


def test_historical_regional_shock_comparison(
    historical_paths: tuple[Path, Path],
    historical_script: ModuleType,
) -> None:
    """Keep calibrated demand-aware settlement when comparing a regional increase."""
    from soybean_sim.scenarios import ProductionShockConfig

    prepared = load_historical_market(*historical_paths)
    crushers, traders, pricing = historical_script.create_settings(prepared)
    before = prepared.model_dump_json()
    comparison = compare_historical_market(
        prepared,
        crusher_configs=crushers,
        trader_configs=traders,
        price_dynamics_config=pricing,
        drought_config=ProductionShockConfig(
            region=Region.BRAZIL, percentage_change=20
        ),
    )
    assert (
        comparison.baseline.initial_state
        == comparison.drought.initial_state
        == prepared.state
    )
    assert all(
        month.consumption_results is not None
        for month in comparison.drought.monthly_results
    )
    assert len(comparison.monthly_metrics()) == 12
    assert prepared.model_dump_json() == before


@pytest.mark.parametrize("change", [-50, 50])
def test_historical_brazil_shock_records_real_flows(
    historical_paths: tuple[Path, Path], historical_script: ModuleType, change: int
) -> None:
    """Run calibrated baseline and shock while preserving all unrelated inputs."""
    from soybean_sim.scenarios import ProductionShockConfig

    prepared = load_historical_market(*historical_paths)
    crushers, traders, pricing = historical_script.create_settings(prepared)
    args = dict(
        crusher_configs=crushers, trader_configs=traders, price_dynamics_config=pricing
    )
    baseline = run_historical_market(prepared, **args)
    modified = run_historical_market(
        prepared,
        **args,
        production_shock=ProductionShockConfig(
            region=Region.BRAZIL,
            percentage_change=change,
            start_month=date(2025, 3, 1),
            end_month=date(2025, 5, 1),
        ),
    )
    assert baseline.initial_state == modified.initial_state == prepared.state
    for before, after in zip(
        baseline.monthly_results, modified.monthly_results, strict=True
    ):
        for original, changed in zip(
            before.market_flows, after.market_flows, strict=True
        ):
            eligible = (
                original.region == Region.BRAZIL
                and original.commodity == Commodity.SOYBEANS
                and original.flow_type == MarketFlowType.SUPPLY
                and date(2025, 3, 1) <= original.month <= date(2025, 5, 1)
            )
            assert changed.quantity_tonnes == pytest.approx(
                original.quantity_tonnes * (1 + change / 100)
                if eligible
                else original.quantity_tonnes
            )


def test_historical_consumption_shock_records_unmet_demand(
    historical_paths: tuple[Path, Path], historical_script: ModuleType
) -> None:
    """Keep shocked requested demand and record the physically unmet portion."""
    from soybean_sim.scenarios import ConsumptionShockConfig

    prepared = load_historical_market(*historical_paths)
    crushers, traders, pricing = historical_script.create_settings(prepared)
    comparison = compare_historical_market(
        prepared,
        crusher_configs=crushers,
        trader_configs=traders,
        price_dynamics_config=pricing,
        drought_config=ConsumptionShockConfig(
            region=Region.EUROPEAN_UNION,
            commodity=Commodity.SOYBEAN_MEAL,
            percentage_change=1000,
        ),
    )
    records = [
        item
        for month in comparison.drought.monthly_results
        for item in (month.consumption_results or ())
        if item.region == Region.EUROPEAN_UNION
        and item.commodity == Commodity.SOYBEAN_MEAL
    ]
    assert len(records) == 12
    assert any(item.unmet_tonnes > 0 for item in records)
    assert all(
        item.requested_tonnes
        == pytest.approx(item.fulfilled_tonnes + item.unmet_tonnes)
        for item in records
    )
    assert comparison.monthly_metrics()[0].markets


def test_historical_crusher_parameter_execution(
    historical_paths: tuple[Path, Path],
    historical_script: ModuleType,
) -> None:
    """Preserve calibrated yields and demand accounting in single and paired runs."""
    from soybean_sim.scenarios import CrusherParameterShockConfig

    prepared = load_historical_market(*historical_paths)
    crushers, traders, pricing = historical_script.create_settings(prepared)
    shock = CrusherParameterShockConfig(
        region=Region.EUROPEAN_UNION, capacity_change_percent=25
    )
    comparison = compare_historical_market(
        prepared,
        crusher_configs=crushers,
        trader_configs=traders,
        price_dynamics_config=pricing,
        drought_config=shock,
    )
    single = run_historical_market(
        prepared,
        crusher_configs=crushers,
        trader_configs=traders,
        price_dynamics_config=pricing,
        crusher_parameter_shock=shock,
    )
    assert single == comparison.drought
    assert comparison.baseline.initial_state == single.initial_state == prepared.state
    assert all(
        a.market_flows == b.market_flows
        for a, b in zip(
            comparison.baseline.monthly_results, single.monthly_results, strict=True
        )
    )
    assert all(
        month.consumption_results is not None for month in single.monthly_results
    )
    assert comparison.modified_crusher_configs[
        Region.EUROPEAN_UNION
    ].monthly_capacity_tonnes == pytest.approx(12.5)
