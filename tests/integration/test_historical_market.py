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
