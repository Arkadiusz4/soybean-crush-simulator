"""Preflight historical inputs and execute the physical market only if feasible."""

import argparse
import math
from collections.abc import Sequence
from pathlib import Path

import historical_market_report as report
import market_smoke_report
import scenario_comparison_report
from rich.console import Console
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

_REPOSITORY = Path(__file__).resolve().parents[1]


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
            options.usda_path, options.world_bank_path, include_historical_trade=True
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
        report.print_boundary_accounting(
            console,
            (
                flow
                for month in result.monthly_results
                for flow in month.external_trade_flows
            ),
            label="Executed Boundary Trade",
        )
        console.print(
            "Physical execution: PASS (complete history and accounting)",
            style="bold green",
        )
        console.print(
            "Monthly activity counts matched interregional trades; "
            "boundary movements are reported separately."
        )
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
            console.print(
                "Baseline/drought comparison: PASS (both complete runs)",
                style="bold green",
            )
            scenario_comparison_report.print_report(
                console,
                comparison,
                completed=[
                    "Historical baseline and drought complete",
                    "Physical accounting validity",
                ],
                verbose=options.verbose,
            )
        else:
            console.print("Baseline/drought comparison: NOT REQUESTED (use --compare)")
        console.print(
            "Overall: PASS (execution validated, not economically calibrated)",
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
