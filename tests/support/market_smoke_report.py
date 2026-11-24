"""Render the Milestone 3 smoke test without changing simulation or validation data."""

import math
from collections.abc import Sequence

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from soybean_sim.models import Commodity
from soybean_sim.simulation import SimulationResult


def print_header(
    console: Console, result: SimulationResult | None = None, *, agent_count: int = 0
) -> None:
    """Introduce the synthetic demonstration and available run metadata.

    Args:
        console (Console): Destination terminal or captured output.
        result (SimulationResult | None): Completed history, absent on early failure.
        agent_count (int): Number of registered agents.

    Returns:
        None: Writes the report header.
    """
    console.rule("Soybean Market Simulator — Milestone 3", style="cyan")
    console.print("End-to-End Smoke Test", style="bold")
    console.print(
        "Synthetic data · Physical market with dynamic regional pricing", style="dim"
    )
    if result is not None:
        first, last = result.monthly_results[0].month, result.monthly_results[-1].month
        regions = sorted(
            {stock.region for stock in result.initial_state.snapshot.inventories}
        )
        names = ", ".join(region.value.replace("_", " ").title() for region in regions)
        console.print(
            f"Period: {first:%Y-%m} to {last:%Y-%m} · Regions ({len(regions)}): {names}"
        )
        console.print(f"Agents: {agent_count}")
    console.print()


def monthly_activity_table(result: SimulationResult) -> Table:
    """Tabulate actual executed activity, including totals over completed months.

    Args:
        result (SimulationResult): Completed market history.

    Returns:
        Table: Monthly crushing and executed trades, with tonne units.
    """
    table = Table(title="Monthly Activity", title_justify="left", box=box.SIMPLE)
    table.add_column("Month", style="bold")
    table.add_column("Crushed (t)", justify="right")
    table.add_column("Executed trades", justify="right")
    table.add_column("Trade volume (t)", justify="right")
    crushing, trade_counts, volumes = [], [], []
    for monthly in result.monthly_results:
        crushed = math.fsum(item.executed_tonnes for item in monthly.settlement_results)
        volume = math.fsum(item.quantity_tonnes for item in monthly.executed_trades)
        count = len(monthly.executed_trades)
        crushing.append(crushed)
        trade_counts.append(count)
        volumes.append(volume)
        table.add_row(
            f"{monthly.month:%Y-%m}", f"{crushed:,.2f}", str(count), f"{volume:,.2f}"
        )
    table.add_section()
    table.add_row(
        "TOTAL",
        f"{math.fsum(crushing):,.2f}",
        str(sum(trade_counts)),
        f"{math.fsum(volumes):,.2f}",
        style="bold",
    )
    return table


def regional_price_table(result: SimulationResult) -> Table:
    """Compare the first and last observed prices, coloring direction rather than merit.

    Args:
        result (SimulationResult): History containing current-month regional prices.

    Returns:
        Table: USD/t values and changes relative to the first observed month.
    """
    first, last = result.monthly_results[0], result.monthly_results[-1]
    baseline = {
        (price.region, price.commodity): price.price_usd_per_tonne
        for price in first.regional_prices
    }
    table = Table(
        title="Regional Price Changes",
        title_justify="left",
        box=box.SIMPLE,
        caption=(
            f"First observed month ({first.month:%Y-%m}) "
            f"to last observed month ({last.month:%Y-%m}). "
            "Colors indicate direction only."
        ),
        caption_justify="left",
    )
    table.add_column("Region")
    table.add_column("Commodity")
    for column in ("Initial\nUSD/t", "Last\nUSD/t", "Change\nUSD/t", "Change\n%"):
        table.add_column(column, justify="right")
    for price in last.regional_prices:
        initial = baseline[price.region, price.commodity]
        change = price.price_usd_per_tonne - initial
        percentage = change / initial * 100
        style = "green" if change > 0 else "red" if change < 0 else ""
        table.add_row(
            price.region.value.replace("_", " ").title(),
            price.commodity.value.replace("_", " ").capitalize(),
            f"{initial:,.2f}",
            f"{price.price_usd_per_tonne:,.2f}",
            Text(f"{change:+,.2f}", style=style),
            Text(f"{percentage:+.2f}%", style=style),
        )
    return table


def inventory_table(result: SimulationResult) -> Table:
    """Show only the final inventories, grouped by region with explicit tonne units.

    Args:
        result (SimulationResult): Completed history and final stocks.

    Returns:
        Table: Soybean, meal, and oil quantities for each represented region.
    """
    table = Table(title="Final Inventories", title_justify="left", box=box.SIMPLE)
    table.add_column("Region")
    for commodity in Commodity:
        table.add_column(
            f"{commodity.value.replace('_', ' ').capitalize()} (t)", justify="right"
        )
    inventories = result.final_state.snapshot.inventories
    quantities = {
        (stock.region, stock.commodity): stock.quantity_tonnes for stock in inventories
    }
    for region in sorted({stock.region for stock in inventories}):
        table.add_row(
            region.value.replace("_", " ").title(),
            *(f"{quantities[region, commodity]:,.2f}" for commodity in Commodity),
        )
    return table


def pressure_table(result: SimulationResult) -> Table:
    """Expand monthly regional pressure into separate supply, demand, and net columns.

    Args:
        result (SimulationResult): Completed monthly pressure observations.

    Returns:
        Table: Quantities in tonnes; net equals supply minus demand.
    """
    table = Table(
        title="Monthly Market Pressures", title_justify="left", box=box.SIMPLE
    )
    for column in ("Month", "Region", "Commodity"):
        table.add_column(column, no_wrap=True)
    for column in ("Supply\n(t)", "Demand\n(t)", "Net supply -\ndemand (t)"):
        table.add_column(column, justify="right")
    for monthly in result.monthly_results:
        for pressure in monthly.market_pressures:
            table.add_row(
                f"{monthly.month:%Y-%m}",
                pressure.region.value.replace("_", " ").title(),
                pressure.commodity.value.replace("_", " ").capitalize(),
                f"{pressure.supply_tonnes:,.2f}",
                f"{pressure.demand_tonnes:,.2f}",
                f"{pressure.supply_tonnes - pressure.demand_tonnes:+,.2f}",
            )
        table.add_section()
    return table


def print_validation_summary(
    console: Console,
    completed: Sequence[str],
    *,
    failed_stage: str | None = None,
    error: Exception | None = None,
) -> None:
    """Summarize completed checks and stop clearly at a failed stage.

    Args:
        console (Console): Destination terminal or captured output.
        completed (Sequence[str]): Successfully completed validation names.
        failed_stage (str | None): Active stage on failure, otherwise None.
        error (Exception | None): Assertion or domain error to display literally.

    Returns:
        None: Writes individual statuses and exactly one overall outcome.
    """
    table = Table(title="Validation Summary", title_justify="left", box=box.SIMPLE)
    table.add_column("Validation")
    table.add_column("Result")
    for stage in completed:
        table.add_row(stage, Text("PASS", style="green"))
    if failed_stage is not None:
        table.add_row(failed_stage, Text("FAIL", style="bold red"))
    console.print(table)
    if failed_stage is not None:
        detail = str(error) or "Assertion failed without an error message"
        console.print(
            Text(f"{failed_stage}: {type(error).__name__}: {detail}", style="red")
        )
        console.print("Validation stopped; remaining checks were not run.", style="dim")
    console.print(
        "Overall: FAIL" if failed_stage is not None else "Overall: PASS",
        style="bold red" if failed_stage is not None else "bold green",
    )


def print_report(
    console: Console,
    result: SimulationResult,
    *,
    agent_count: int,
    completed: Sequence[str],
    verbose: bool = False,
) -> None:
    """Render the completed demonstration in presentation order.

    Args:
        console (Console): Destination terminal or captured output.
        result (SimulationResult): Validated market history.
        agent_count (int): Number of registered agents.
        completed (Sequence[str]): Successfully completed validation stages.
        verbose (bool): Include detailed monthly pressure observations.

    Returns:
        None: Writes the report without changing input models or calculations.
    """
    print_header(console, result, agent_count=agent_count)
    for table in (
        monthly_activity_table(result),
        regional_price_table(result),
        inventory_table(result),
    ):
        console.print(table)
        console.print()
    if verbose:
        console.print(pressure_table(result))
        console.print()
    print_validation_summary(console, completed)
