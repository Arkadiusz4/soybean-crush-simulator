"""Preflight historical inputs and execute the physical market only if feasible."""

import argparse
import math
from collections.abc import Sequence
from pathlib import Path

import historical_market_report as report
import market_smoke_report
from rich.console import Console
from rich.table import Table
from smoke_test_market import check_accounting

from soybean_sim.agents import CrusherConfig, TraderConfig
from soybean_sim.market import PriceDynamicsConfig
from soybean_sim.models import Commodity, Region
from soybean_sim.scenarios import (
    ArgentinaDroughtConfig,
    HistoricalMarketError,
    HistoricalMarketInputs,
    calibrate_historical_crushers,
    compare_historical_market,
    load_historical_market,
    preflight_historical_market,
    run_historical_market,
)
from soybean_sim.simulation import SimulationResult

_REPOSITORY = Path(__file__).resolve().parents[2]


def print_demand(console: Console, result: SimulationResult, *, label: str) -> None:
    """Report modeled requested, fulfilled, and unmet consumption by commodity.

    Args:
        console (Console): Terminal output destination.
        result (SimulationResult): Completed demand-aware simulation.
        label (str): Baseline or drought run label.

    Returns:
        None: Writes totals without treating unmet demand as consumption.
    """
    table = Table(title=f"{label} Consumption (t)", title_justify="left")
    for heading in ("Commodity", "Requested", "Fulfilled", "Unmet"):
        table.add_column(heading)
    records = []
    for month in result.monthly_results:
        if month.consumption_results is None:
            raise AssertionError(f"{label}: missing demand settlement results")
        records.extend(month.consumption_results)
    totals = []
    for commodity in sorted(Commodity):
        selected = [item for item in records if item.commodity == commodity]
        requested = math.fsum(item.requested_tonnes for item in selected)
        fulfilled = math.fsum(item.fulfilled_tonnes for item in selected)
        unmet = math.fsum(item.unmet_tonnes for item in selected)
        assert math.isclose(requested, fulfilled + unmet, rel_tol=1e-12, abs_tol=1e-8)
        totals.append((requested, fulfilled, unmet))
        table.add_row(
            commodity.value, f"{requested:,.2f}", f"{fulfilled:,.2f}", f"{unmet:,.2f}"
        )
    table.add_row(
        "TOTAL", *(f"{math.fsum(values):,.2f}" for values in zip(*totals, strict=True))
    )
    console.print(table)


def create_settings(
    prepared: HistoricalMarketInputs,
) -> tuple[
    dict[Region, CrusherConfig], dict[Region, TraderConfig], PriceDynamicsConfig
]:
    """Scale processing to observed crush while retaining explicit strategy assumptions.

    Args:
        prepared (HistoricalMarketInputs): Annual crush and reference-price baselines.

    Returns:
        tuple: Inspectable regional Crusher/Trader rules and default price dynamics.
    """
    prices = {
        price.commodity: price.price_usd_per_tonne
        for price in prepared.reference_prices
    }
    crushers = calibrate_historical_crushers(
        prepared, processing_cost_usd_per_tonne=35, calibrate_yields=True
    )
    traders = {
        region: TraderConfig(
            reference_soybean_price_usd_per_tonne=prices[Commodity.SOYBEANS],
            reference_soybean_meal_price_usd_per_tonne=prices[Commodity.SOYBEAN_MEAL],
            reference_soybean_oil_price_usd_per_tonne=prices[Commodity.SOYBEAN_OIL],
            maximum_monthly_purchase_tonnes=5000,
        )
        for region in Region
    }
    return crushers, traders, PriceDynamicsConfig()


def main(argv: Sequence[str] | None = None) -> int:
    """Distinguish preparation, preflight, physical execution, and optional comparison.

    Args:
        argv (Sequence[str] | None): Script arguments; None reads the command line.

    Returns:
        int: Zero for complete requested runs, one for blocking data/model diagnostics.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--usda-path", type=Path, default=_REPOSITORY / "data/raw/psd_oilseeds.csv"
    )
    parser.add_argument(
        "--world-bank-path",
        type=Path,
        default=_REPOSITORY / "data/raw/CMO-Historical-Data-Monthly.xlsx",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show provenance and annual/monthly source detail.",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Attempt paired 30% drought comparison after a feasible baseline.",
    )
    options = parser.parse_args(argv)
    console = Console()
    stage = "Historical data preparation"
    try:
        prepared = load_historical_market(
            options.usda_path, options.world_bank_path, include_historical_trade=False
        )
        stage = "Historical capacity calibration"
        crushers, traders, pricing = create_settings(prepared)
        report.print_inputs(
            console, prepared, crushers, traders, pricing, verbose=options.verbose
        )
        stage = "Historical preflight"
        checked = preflight_historical_market(
            prepared,
            crusher_configs=crushers,
            trader_configs=traders,
            price_dynamics_config=pricing,
            calibration=True,
        )
        report.print_preflight(console, checked)
        if not checked.can_run:
            console.print(
                "Physical execution: NOT RUN (preflight blocked)", style="bold red"
            )
            console.print("Baseline/drought comparison: NOT RUN (baseline blocked)")
            console.print("Overall: BLOCKED", style="bold red")
            return 1
        stage = "Historical physical execution"
        console.print(
            "Historical calibration: demand-aware settlement; "
            "agent trade matching enabled"
        )
        result = run_historical_market(
            prepared,
            crusher_configs=crushers,
            trader_configs=traders,
            price_dynamics_config=pricing,
        )
        stage = "Historical inventory accounting"
        configs = {
            f"crusher-{region.value}": config for region, config in crushers.items()
        }
        check_accounting(result, configs)
        assert len(result.monthly_results) == len(prepared.metadata.months)
        residual = math.fsum(
            item.residual_output_tonnes
            for month in result.monthly_results
            for item in month.settlement_results
        )
        console.print(
            "Executed processing residual: "
            f"{residual:,.2f} t "
            "(unmodeled output, outside traded inventories)."
        )
        console.print(
            "Physical execution: PASS (complete history and accounting)",
            style="bold green",
        )
        console.print(f"Simulated months: {len(result.monthly_results)}")
        print_demand(console, result, label="Baseline")
        for table in (
            market_smoke_report.monthly_activity_table(result),
            market_smoke_report.regional_price_table(result),
            market_smoke_report.inventory_table(result),
        ):
            console.print(table)
        if options.compare:
            stage = "Historical baseline/drought comparison"
            comparison = compare_historical_market(
                prepared,
                crusher_configs=crushers,
                trader_configs=traders,
                price_dynamics_config=pricing,
                drought_config=ArgentinaDroughtConfig(),
            )
            check_accounting(comparison.baseline, configs)
            check_accounting(comparison.drought, configs)
            assert comparison.baseline == result
            assert len(comparison.drought.monthly_results) == len(
                prepared.metadata.months
            )
            console.print(
                "Baseline/drought comparison: PASS (both complete runs)",
                style="bold green",
            )
            print_demand(console, comparison.drought, label="Drought")
            trade_count = sum(
                len(month.executed_trades)
                for month in comparison.drought.monthly_results
            )
            console.print(
                f"Drought simulated months: {len(comparison.drought.monthly_results)}; "
                f"executed trades: {trade_count}"
            )
        else:
            console.print("Baseline/drought comparison: NOT REQUESTED (use --compare)")
        console.print(
            "Overall: PASS (modeled scenario; not a historical reconstruction "
            "or forecast)",
            style="bold green",
        )
        return 0
    except HistoricalMarketError as exc:
        console.print(f"{stage}: BLOCKED", style="bold red")
        report.print_preflight(console, exc.diagnostics)
        console.print("No complete result is returned for the blocked run.")
        if options.compare and stage != "Historical baseline/drought comparison":
            console.print("Baseline/drought comparison: NOT RUN (baseline blocked)")
        console.print("Overall: BLOCKED", style="bold red")
        return 1
    except AssertionError as exc:
        console.print(f"{stage}: FAIL", style="bold red")
        console.print(str(exc), markup=False)
        console.print("Overall: FAIL", style="bold red")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
