"""Present source assumptions and historical feasibility without implying execution."""

import math
from collections.abc import Iterable, Mapping

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from soybean_sim.agents import CrusherConfig, TraderConfig
from soybean_sim.market import ExternalTradeFlow, MarketFlowType, PriceDynamicsConfig
from soybean_sim.models import Commodity, Region
from soybean_sim.scenarios import HistoricalMarketInputs, HistoricalPreflight


def print_inputs(
    console: Console,
    prepared: HistoricalMarketInputs,
    crushers: Mapping[Region, CrusherConfig],
    traders: Mapping[Region, TraderConfig],
    pricing: PriceDynamicsConfig,
    *,
    verbose: bool = False,
) -> None:
    """Label original observations separately from modeled stocks, flows, and settings.

    Args:
        console (Console): Terminal or captured output.
        prepared (HistoricalMarketInputs): Observations and source provenance.
        crushers (Mapping[Region, CrusherConfig]): Explicit regional processors.
        traders (Mapping[Region, TraderConfig]): Explicit regional trading assumptions.
        pricing (PriceDynamicsConfig): Dynamic price assumptions.
        verbose (bool): Show hashes, annual values, allocations, and first-month flows.

    Returns:
        None: Writes inspectable source and parameter tables.
    """
    metadata = prepared.metadata
    console.rule("Soybean Market Simulator — Historical Physical Market", style="cyan")
    console.print(
        f"USDA marketing year: {metadata.marketing_year} · "
        f"World Bank observations: {metadata.reference_price_month}"
    )
    console.print(
        f"Simulation: {metadata.months[0]:%Y-%m} to {metadata.months[-1]:%Y-%m} · "
        "Four regions, three commodities"
    )
    console.print(Text(f"USDA source: {metadata.usda_source}"))
    console.print(Text(f"World Bank source: {metadata.world_bank_source}"))
    console.print(
        "Data preparation: PASS (selection and modeled allocation only)", style="green"
    )
    prices = Table(
        title="Initial Global Reference Prices", title_justify="left", box=box.SIMPLE
    )
    prices.add_column("Commodity")
    prices.add_column("Observed month")
    prices.add_column("USD/t", justify="right")
    for price in prepared.reference_prices:
        prices.add_row(
            price.commodity.value.replace("_", " "),
            str(price.month),
            f"{price.price_usd_per_tonne:,.2f}",
        )
    console.print(prices)
    settings = Table(
        title="Explicit Agent Settings — Modeling Assumptions",
        title_justify="left",
        box=box.SIMPLE,
    )
    for heading in (
        "Region",
        "Crusher capacity\nt/month",
        "Cost\nUSD/t",
        "Meal/oil\nyields",
        "Residual\nyield",
        "Trader limit\nt/month",
    ):
        settings.add_column(heading)
    for region in sorted(Region):
        config = crushers[region]
        settings.add_row(
            region.value.replace("_", " ").title(),
            f"{config.monthly_capacity_tonnes:,.0f}",
            f"{config.processing_cost_usd_per_tonne:g}",
            f"{config.meal_yield_fraction:.3%}/{config.oil_yield_fraction:.3%}",
            f"{config.residual_yield_fraction:.3%}",
            f"{traders[region].maximum_monthly_purchase_tonnes:,.0f}",
        )
    console.print(settings)
    if metadata.include_historical_trade:
        console.print(
            "Trade mode: explicit clearing boundary; annual imports/exports / 12. "
            "Agent trade matching is disabled. These are modeled gross transfers, "
            "not observed monthly trade or identified bilateral routes."
        )
        print_boundary_accounting(
            console,
            prepared.external_trade_flows,
            label="Scheduled Modeled Boundary Trade",
        )
    console.print(
        "Crusher capacity = selected annual USDA soybean crush / 12. "
        "This is an assumed throughput limit, not measured nameplate capacity; "
        "margin rules may reduce execution. Historical product yields use production "
        "divided by crush without normalization; the residual is recorded separately. "
        "Processing cost remains an assumption."
    )
    _print_processing_requirements(console, prepared, crushers)
    console.print(
        "Producer/Trader references use the selected World Bank values; "
        "default decision bands apply."
    )
    console.print(
        f"Price sensitivity {pricing.price_sensitivity:g}; "
        f"monthly cap {pricing.maximum_monthly_change:.0%}; "
        f"price floor {pricing.minimum_price_usd_per_tonne:g} USD/t; "
        f"supply floor {pricing.supply_floor_tonnes:g} t."
    )
    if verbose:
        _print_source_detail(console, prepared)


def _print_processing_requirements(
    console: Console,
    prepared: HistoricalMarketInputs,
    crushers: Mapping[Region, CrusherConfig],
) -> None:
    """Compare first-month soybean availability with product-driven processing needs.

    Args:
        console (Console): Terminal output destination.
        prepared (HistoricalMarketInputs): Unchanged starting stocks and monthly flows.
        crushers (Mapping[Region, CrusherConfig]): Selected processing yields.

    Returns:
        None: Prints input tonnes needed after using opening product stocks.
    """
    stocks = {
        (item.region, item.commodity): item.quantity_tonnes
        for item in prepared.state.snapshot.inventories
    }
    flows = {
        (item.region, item.commodity, item.flow_type): item.quantity_tonnes
        for item in prepared.flows
        if item.month == prepared.metadata.months[0]
    }
    boundary = {
        (item.region, item.commodity, item.is_import): item.quantity_tonnes
        for item in prepared.external_trade_flows
        if item.month == prepared.metadata.months[0]
    }
    table = Table(
        title=f"Processing Feasibility — {prepared.metadata.months[0]} (t)",
        title_justify="left",
        box=box.SIMPLE,
        caption="Input required covers consumption and scheduled exports after "
        "opening product stocks and imports. Soybean export commitments are reserved.",
    )
    for heading in ("Region", "Soybeans available", "Soybean input required"):
        table.add_column(heading)
    for region in sorted(Region):
        required = []
        config = crushers[region]
        for commodity, fraction in (
            (Commodity.SOYBEAN_MEAL, config.meal_yield_fraction),
            (Commodity.SOYBEAN_OIL, config.oil_yield_fraction),
        ):
            deficit = max(
                flows[region, commodity, MarketFlowType.CONSUMPTION]
                + boundary.get((region, commodity, False), 0)
                - boundary.get((region, commodity, True), 0)
                - stocks[region, commodity],
                0,
            )
            if deficit == 0:
                required.append(0.0)
            elif fraction == 0:
                required.append(math.inf)
            else:
                required.append(deficit / fraction)
        soybeans = (
            stocks[region, Commodity.SOYBEANS]
            + flows[region, Commodity.SOYBEANS, MarketFlowType.SUPPLY]
            + boundary.get((region, Commodity.SOYBEANS, True), 0)
            - boundary.get((region, Commodity.SOYBEANS, False), 0)
        )
        input_needed = max(required)
        table.add_row(
            region.value,
            f"{soybeans:,.2f}",
            f"{input_needed:,.2f}"
            if math.isfinite(input_needed)
            else "Unavailable yield",
        )
    console.print(table)


def print_boundary_accounting(
    console: Console,
    flows: Iterable[ExternalTradeFlow],
    *,
    label: str,
) -> None:
    """Show both sides of the open-boundary mass ledger without inventing outside stock.

    Args:
        console (Console): Terminal output destination.
        flows (Iterable[ExternalTradeFlow]): Scheduled or fully executed transfers.
        label (str): Explicit status distinguishing schedules from actual executions.

    Returns:
        None: Prints imports, exports, and the signed external counterpart in tonnes.
    """
    records = tuple(flows)
    table = Table(title=label + " (t)", title_justify="left", box=box.SIMPLE)
    for heading in (
        "Commodity",
        "Regional imports",
        "Regional exports",
        "External net",
    ):
        table.add_column(heading)
    for commodity in sorted(Commodity):
        imports = math.fsum(
            item.quantity_tonnes
            for item in records
            if item.commodity == commodity and item.is_import
        )
        exports = math.fsum(
            item.quantity_tonnes
            for item in records
            if item.commodity == commodity and not item.is_import
        )
        table.add_row(
            commodity.value,
            f"{imports:,.2f}",
            f"{exports:,.2f}",
            f"{exports - imports:,.2f}",
        )
    console.print(table)
    console.print(
        "External net is exports minus imports, not an outside inventory level."
    )


def print_preflight(console: Console, report: HistoricalPreflight) -> None:
    """Display warnings and blocking errors as distinct outcomes.

    Args:
        console (Console): Terminal or captured output.
        report (HistoricalPreflight): Screening or execution diagnostic information.

    Returns:
        None: Writes diagnostics without claiming physical simulation success.
    """
    console.print(
        "Preflight: CLEAR (screen only, not completed execution)"
        if report.can_run
        else "Preflight / execution diagnostics: BLOCKED",
        style="bold green" if report.can_run else "bold red",
    )
    for warning in report.warnings:
        console.print(Text(f"WARNING: {warning}", style="yellow"))
    for error in report.blocking_errors:
        console.print(Text(f"BLOCKING: {error}", style="red"))


def _print_source_detail(console: Console, prepared: HistoricalMarketInputs) -> None:
    """Expand provenance and original annual/first-month values without dumping objects.

    Args:
        console (Console): Terminal or capture destination.
        prepared (HistoricalMarketInputs): Original and modeled quantities.

    Returns:
        None: Writes hashes, allocation assumptions, and clearly dated data tables.
    """
    metadata = prepared.metadata
    console.print(Text(f"USDA SHA-256: {metadata.usda_sha256 or 'not a local file'}"))
    console.print(
        Text(f"World Bank SHA-256: {metadata.world_bank_sha256 or 'not a local file'}")
    )
    console.print(
        f"Modeled January-December supply weights: {metadata.allocation.supply_weights}"
    )
    console.print(
        "Modeled January-December consumption weights: "
        f"{metadata.allocation.consumption_weights}"
    )
    annual = Table(
        title=f"Original USDA Marketing-Year {metadata.marketing_year} Values (t)",
        title_justify="left",
        box=box.SIMPLE,
    )
    for heading in (
        "Region",
        "Commodity",
        "Production",
        "Domestic consumption",
        "Beginning stocks",
        "Crush input",
    ):
        annual.add_column(heading)
    for balance in prepared.balances:
        annual.add_row(
            balance.region.value,
            balance.commodity.value,
            str(balance.production_tonnes),
            str(balance.domestic_consumption_tonnes),
            str(balance.beginning_stocks_tonnes),
            str(balance.crush_tonnes),
        )
    console.print(annual)
    flows = Table(
        title=(
            f"Modeled Allocations — {metadata.months[0]} (t), Not Observed Monthly Data"
        ),
        title_justify="left",
        box=box.SIMPLE,
    )
    for heading in ("Region", "Commodity", "Type", "Quantity (t)"):
        flows.add_column(heading)
    for flow in prepared.flows:
        if flow.month == metadata.months[0]:
            flows.add_row(
                flow.region.value,
                flow.commodity.value,
                flow.flow_type.value,
                f"{flow.quantity_tonnes:,.2f}",
            )
    console.print(flows)
