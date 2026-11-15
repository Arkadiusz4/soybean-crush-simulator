"""Render controlled baseline/drought observations and executed activity."""

import math
from collections.abc import Sequence

import market_smoke_report
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from soybean_sim.market import MarketFlowType
from soybean_sim.models import Commodity, Region
from soybean_sim.scenarios import (
    MetricComparison,
    MonthlyComparison,
    ScenarioComparison,
)


def print_header(
    console: Console, comparison: ScenarioComparison | None = None
) -> None:
    """Identify the synthetic experiment and its common observed period.

    Args:
        console (Console): Terminal or capture destination.
        comparison (ScenarioComparison | None): Completed histories if available.

    Returns:
        None: Writes clearly labeled comparison metadata.
    """
    console.rule("Soybean Market Simulator", style="cyan")
    console.print("Baseline vs Argentina Drought", style="bold")
    console.print(
        "Synthetic data · Controlled scenario experiment, not a forecast", style="dim"
    )
    if comparison is not None:
        months = comparison.baseline.monthly_results
        regions = sorted(
            {
                stock.region
                for stock in comparison.baseline.initial_state.snapshot.inventories
            }
        )
        console.print(f"Period: {months[0].month:%Y-%m} to {months[-1].month:%Y-%m}")
        console.print(
            "Regions: "
            + ", ".join(region.value.replace("_", " ").title() for region in regions)
        )
        console.print(
            "Drought reduction: "
            f"{comparison.drought_config.production_reduction_fraction:.0%}"
        )
    console.print()


def _change(value: float) -> Text:
    """Format a signed difference using directional rather than economic colors.

    Args:
        value (float): Signed difference in the containing table's units.

    Returns:
        Text: Green positive, red negative, or neutral zero value.
    """
    style = "green" if value > 0 else "red" if value < 0 else ""
    return Text(f"{value:+,.2f}", style=style)


def production_table(comparison: ScenarioComparison) -> Table:
    """Sum actual Argentina soybean supply inputs rather than stock changes.

    Args:
        comparison (ScenarioComparison): Complete paired monthly input histories.

    Returns:
        Table: Total production inputs and absolute/percentage differences.
    """
    quantities = [
        math.fsum(
            flow.quantity_tonnes
            for month in result.monthly_results
            for flow in month.market_flows
            if (flow.region, flow.commodity, flow.flow_type)
            == (Region.ARGENTINA, Commodity.SOYBEANS, MarketFlowType.SUPPLY)
        )
        for result in (comparison.baseline, comparison.drought)
    ]
    pair = MetricComparison(baseline=quantities[0], drought=quantities[1])
    table = Table(title="Scenario Overview", title_justify="left", box=box.SIMPLE)
    for heading in (
        "Argentina production (t)",
        "Baseline",
        "Drought",
        "Difference",
        "Difference %",
    ):
        table.add_column(
            heading, justify="left" if heading.startswith("Argentina") else "right"
        )
    percentage = pair.percentage_difference
    table.add_row(
        "Soybean supply",
        f"{pair.baseline:,.2f}",
        f"{pair.drought:,.2f}",
        _change(pair.difference),
        "n/a" if percentage is None else f"{percentage:+.2f}%",
    )
    return table


def price_table(monthly: MonthlyComparison) -> Table:
    """Compare the same final observed month, preserving zero price differences.

    Args:
        monthly (MonthlyComparison): Same-month regional observations.

    Returns:
        Table: Regional USD/t values and drought-minus-baseline changes.
    """
    table = Table(
        title=f"Regional Price Comparison — observed {monthly.month:%Y-%m}",
        title_justify="left",
        box=box.SIMPLE,
        caption=(
            "Observed during the month, not a post-simulation price. "
            "Colors show direction only."
        ),
        caption_justify="left",
    )
    table.add_column("Region", no_wrap=True)
    table.add_column("Commodity", no_wrap=True)
    for heading in (
        "Baseline\nUSD/t",
        "Drought\nUSD/t",
        "Change\nUSD/t",
        "Change\n%",
    ):
        table.add_column(heading, justify="right")
    for item in monthly.markets:
        pair = item.price_usd_per_tonne
        table.add_row(
            item.region.value.replace("_", " ").title(),
            item.commodity.value.replace("_", " ").capitalize(),
            f"{pair.baseline:,.2f}",
            f"{pair.drought:,.2f}",
            _change(pair.difference),
            f"{pair.percentage_difference:+.2f}%",
        )
    return table


def physical_table(metrics: tuple[MonthlyComparison, ...]) -> Table:
    """Aggregate actual executed quantities, counting each trade only once.

    Args:
        metrics (tuple[MonthlyComparison, ...]): Chronological derived metrics.

    Returns:
        Table: Period crushing, executed trade volume, and execution counts.
    """
    crushing = MetricComparison(
        baseline=math.fsum(
            pair.baseline for month in metrics for _, pair in month.crushing_tonnes
        ),
        drought=math.fsum(
            pair.drought for month in metrics for _, pair in month.crushing_tonnes
        ),
    )
    trade = MetricComparison(
        baseline=math.fsum(month.trade_volume_tonnes.baseline for month in metrics),
        drought=math.fsum(month.trade_volume_tonnes.drought for month in metrics),
    )
    counts = (
        sum(month.baseline_trade_count for month in metrics),
        sum(month.drought_trade_count for month in metrics),
    )
    table = Table(
        title="Physical Market Comparison", title_justify="left", box=box.SIMPLE
    )
    for heading in ("Metric", "Baseline", "Drought", "Difference"):
        table.add_column(heading, justify="left" if heading == "Metric" else "right")
    for name, pair in (
        ("Executed crushing (t)", crushing),
        ("Executed trade volume (t)", trade),
    ):
        table.add_row(
            name,
            f"{pair.baseline:,.2f}",
            f"{pair.drought:,.2f}",
            _change(pair.difference),
        )
    table.add_row(
        "Executed trade count",
        str(counts[0]),
        str(counts[1]),
        f"{counts[1] - counts[0]:+d}",
    )
    return table


def inventory_table(monthly: MonthlyComparison) -> Table:
    """Show selected ending stocks, retaining unchanged regions explicitly.

    Args:
        monthly (MonthlyComparison): Last completed monthly observations.

    Returns:
        Table: All regional soybeans and Argentina's meal/oil stocks, in tonnes.
    """
    table = Table(
        title=f"Selected Ending Inventories — {monthly.month:%Y-%m} (t)",
        title_justify="left",
        box=box.SIMPLE,
    )
    for heading in ("Region", "Commodity", "Baseline", "Drought", "Difference"):
        table.add_column(
            heading, justify="left" if heading in ("Region", "Commodity") else "right"
        )
    for item in monthly.markets:
        if item.commodity != Commodity.SOYBEANS and item.region != Region.ARGENTINA:
            continue
        pair = item.inventory_tonnes
        table.add_row(
            item.region.value.replace("_", " ").title(),
            item.commodity.value.replace("_", " ").capitalize(),
            f"{pair.baseline:,.2f}",
            f"{pair.drought:,.2f}",
            _change(pair.difference),
        )
    return table


def print_monthly_tables(
    console: Console, metrics: tuple[MonthlyComparison, ...]
) -> None:
    """Expand observed prices, crushing, trades, and selected inventory trajectories.

    Args:
        console (Console): Terminal or capture destination.
        metrics (tuple[MonthlyComparison, ...]): Chronological monthly comparisons.

    Returns:
        None: Writes labeled tables without raw model representations.
    """
    for commodity in Commodity:
        table = Table(
            title=f"Monthly {commodity.value.replace('_', ' ')} prices (USD/t)",
            title_justify="left",
            box=box.SIMPLE,
        )
        for heading in ("Month", "Region", "Baseline", "Drought", "Difference"):
            table.add_column(heading)
        for monthly in metrics:
            for item in monthly.markets:
                if item.commodity == commodity:
                    pair = item.price_usd_per_tonne
                    table.add_row(
                        f"{monthly.month:%Y-%m}",
                        item.region.value.replace("_", " ").title(),
                        f"{pair.baseline:,.2f}",
                        f"{pair.drought:,.2f}",
                        _change(pair.difference),
                    )
        console.print(table)
    _print_monthly_activity(console, metrics)


def _print_monthly_activity(
    console: Console, metrics: tuple[MonthlyComparison, ...]
) -> None:
    """Render separate executed-activity and stock-trajectory tables.

    Args:
        console (Console): Terminal or capture destination.
        metrics (tuple[MonthlyComparison, ...]): Validated monthly comparisons.

    Returns:
        None: Writes the three activity tables.
    """
    for table in (
        _monthly_crushing_table(metrics),
        _monthly_trade_table(metrics),
        _monthly_inventory_table(metrics),
    ):
        console.print(table)


def _monthly_crushing_table(metrics: tuple[MonthlyComparison, ...]) -> Table:
    """Keep regional executed input quantities separate from processing requests.

    Args:
        metrics (tuple[MonthlyComparison, ...]): Chronological activity comparisons.

    Returns:
        Table: Monthly executed crushing in tonnes for each region.
    """
    table = Table(title="Monthly crushing (t)", title_justify="left", box=box.SIMPLE)
    for heading in ("Month", "Region", "Baseline", "Drought", "Difference"):
        table.add_column(heading)
    for monthly in metrics:
        for region, pair in monthly.crushing_tonnes:
            table.add_row(
                f"{monthly.month:%Y-%m}",
                region.value.replace("_", " ").title(),
                f"{pair.baseline:,.2f}",
                f"{pair.drought:,.2f}",
                _change(pair.difference),
            )
    return table


def _monthly_trade_table(metrics: tuple[MonthlyComparison, ...]) -> Table:
    """Show matched volume once per month instead of summing imports and exports.

    Args:
        metrics (tuple[MonthlyComparison, ...]): Chronological trade comparisons.

    Returns:
        Table: Monthly total executed trade tonnes.
    """
    table = Table(
        title="Monthly executed trade volume (t)", title_justify="left", box=box.SIMPLE
    )
    for heading in ("Month", "Baseline", "Drought", "Difference"):
        table.add_column(heading)
    for monthly in metrics:
        pair = monthly.trade_volume_tonnes
        table.add_row(
            f"{monthly.month:%Y-%m}",
            f"{pair.baseline:,.2f}",
            f"{pair.drought:,.2f}",
            _change(pair.difference),
        )
    return table


def _monthly_inventory_table(metrics: tuple[MonthlyComparison, ...]) -> Table:
    """Limit detailed stock trajectories to soybeans in each represented region.

    Args:
        metrics (tuple[MonthlyComparison, ...]): Chronological ending-stock comparisons.

    Returns:
        Table: Selected monthly ending soybean inventories in tonnes.
    """
    table = Table(
        title="Monthly selected soybean inventories (t)",
        title_justify="left",
        box=box.SIMPLE,
    )
    for heading in ("Month", "Region", "Baseline", "Drought", "Difference"):
        table.add_column(heading)
    for monthly in metrics:
        for item in monthly.markets:
            if item.commodity == Commodity.SOYBEANS:
                pair = item.inventory_tonnes
                table.add_row(
                    f"{monthly.month:%Y-%m}",
                    item.region.value.replace("_", " ").title(),
                    f"{pair.baseline:,.2f}",
                    f"{pair.drought:,.2f}",
                    _change(pair.difference),
                )
    return table


def print_report(
    console: Console,
    comparison: ScenarioComparison,
    *,
    completed: Sequence[str],
    verbose: bool = False,
) -> None:
    """Render same-period observed comparisons and their validation status.

    Args:
        console (Console): Terminal or capture destination.
        comparison (ScenarioComparison): Validated paired histories.
        completed (Sequence[str]): Successful validation names.
        verbose (bool): Include detailed monthly trajectories.

    Returns:
        None: Writes the default classroom report and optional detail.
    """
    metrics = comparison.monthly_metrics()
    print_header(console, comparison)
    for table in (
        production_table(comparison),
        price_table(metrics[-1]),
        physical_table(metrics),
        inventory_table(metrics[-1]),
    ):
        console.print(table)
        console.print()
    if all(month.trade_volume_tonnes.difference == 0 for month in metrics):
        console.print(
            "Trade volume is unchanged: the implemented intentions remain "
            "fulfillable in both runs.",
            style="dim",
        )
    if all(
        pair.difference == 0 for month in metrics for _, pair in month.crushing_tonnes
    ):
        console.print(
            "Crushing is unchanged under the implemented margin/capacity rule.",
            style="dim",
        )
    if verbose:
        print_monthly_tables(console, metrics)
    market_smoke_report.print_validation_summary(console, completed)


def print_failure(
    console: Console, completed: Sequence[str], stage: str, error: Exception
) -> None:
    """Identify a failed comparison stage and avoid an overall PASS.

    Args:
        console (Console): Error-output destination.
        completed (Sequence[str]): Completed validation stages.
        stage (str): Active stage when validation stopped.
        error (Exception): Assertion or domain failure to display literally.

    Returns:
        None: Writes the failed validation summary.
    """
    print_header(console)
    market_smoke_report.print_validation_summary(
        console, completed, failed_stage=stage, error=error
    )
