"""Explore recorded monthly market activity without rerunning the simulation."""

import math
from datetime import date

import pandas as pd
import streamlit as st

from soybean_sim.agents import DecisionAction
from soybean_sim.market import SettlementOutcome
from soybean_sim.models import Commodity, Region
from soybean_sim.simulation import SimulationResult


def _coverage(result: SimulationResult) -> tuple[list[Region], list[Commodity]]:
    """Find regions and commodities represented by recorded market observations."""
    regions: set[Region] = set()
    commodities: set[Commodity] = set()
    for month in result.monthly_results:
        for record in (
            *month.starting_state.snapshot.inventories,
            *month.ending_state.snapshot.inventories,
            *month.regional_prices,
            *month.market_pressures,
        ):
            regions.add(record.region)
            commodities.add(record.commodity)
        for trade in month.executed_trades:
            regions.update((trade.source_region, trade.destination_region))
            commodities.add(trade.commodity)
    return sorted(regions), sorted(commodities)


def _price_series(
    result: SimulationResult, region: Region, commodity: Commodity
) -> list[tuple[date, float | None]]:
    """Return observed regional prices with missing months retained as gaps."""
    return [
        (
            month.month,
            next(
                (
                    price.price_usd_per_tonne
                    for price in month.regional_prices
                    if price.region == region
                    and price.commodity == commodity
                    and math.isfinite(price.price_usd_per_tonne)
                ),
                None,
            ),
        )
        for month in sorted(result.monthly_results, key=lambda item: item.month)
    ]


def _crushing_series(
    result: SimulationResult, region: Region
) -> list[tuple[date, float | None]]:
    """Sum settled soybean input for each month in the selected region."""
    series = []
    for month in sorted(result.monthly_results, key=lambda item: item.month):
        values = [
            settlement.executed_tonnes
            for settlement in month.settlement_results
            if settlement.decision.agent.region == region
            and settlement.decision.action == DecisionAction.CRUSH
            and settlement.outcome == SettlementOutcome.CRUSH_SETTLED
        ]
        series.append(
            (
                month.month,
                math.fsum(values) if all(map(math.isfinite, values)) else None,
            )
        )
    return series


def _inventory_series(
    result: SimulationResult, region: Region, commodity: Commodity
) -> list[tuple[date, float | None]]:
    """Return recorded month-end stocks, leaving absent inventories as gaps."""
    return [
        (
            month.month,
            next(
                (
                    stock.quantity_tonnes
                    for stock in month.ending_state.snapshot.inventories
                    if stock.region == region
                    and stock.commodity == commodity
                    and math.isfinite(stock.quantity_tonnes)
                ),
                None,
            ),
        )
        for month in sorted(result.monthly_results, key=lambda item: item.month)
    ]


def _trade_series(
    result: SimulationResult, region: Region, commodity: Commodity
) -> list[tuple[date, float | None, float | None]]:
    """Sum executed interregional imports and exports for each simulated month."""
    series = []
    for month in sorted(result.monthly_results, key=lambda item: item.month):
        imports = [
            trade.quantity_tonnes
            for trade in month.executed_trades
            if trade.commodity == commodity and trade.destination_region == region
        ]
        exports = [
            trade.quantity_tonnes
            for trade in month.executed_trades
            if trade.commodity == commodity and trade.source_region == region
        ]
        incoming = math.fsum(imports) if all(map(math.isfinite, imports)) else None
        outgoing = math.fsum(exports) if all(map(math.isfinite, exports)) else None
        series.append((month.month, incoming, outgoing))
    return series


def _label(value: Region | Commodity) -> str:
    """Format a domain identifier for a selector."""
    return value.value.replace("_", " ").title()


def _selector(
    label: str, options: list[Region] | list[Commodity], key: str
) -> Region | Commodity:
    """Reset stale widget choices before selecting from current result coverage."""
    if key in st.session_state and st.session_state[key] not in options:
        del st.session_state[key]
    return st.selectbox(label, options, format_func=_label, key=key)


def _line_section(
    title: str, series: list[tuple[date, float | None]], value_label: str
) -> None:
    """Display recorded values and distinguish missing observations from zero."""
    st.subheader(title)
    if not any(value is not None for _, value in series):
        st.info("No observations are available for this selection.")
        return
    frame = pd.DataFrame(series, columns=["Month", value_label]).set_index("Month")
    st.line_chart(frame, y_label=value_label, x_label="Month")
    if any(value is None for _, value in series):
        st.caption("Missing observations are shown as gaps.")


def _prices(result: SimulationResult, region: Region, commodity: Commodity) -> None:
    """Show regional prices observed during simulated months and their change."""
    series = _price_series(result, region, commodity)
    _line_section("Regional Price Trends", series, "Observed price (USD/t)")
    observed = [value for _, value in series if value is not None]
    if not observed:
        return
    first, last = observed[0], observed[-1]
    change = last - first
    percentage = f"{change / first * 100:+,.2f}%" if first else "Unavailable"
    st.caption(
        f"First: ${first:,.2f}/t · Last: ${last:,.2f}/t · "
        f"Change: ${change:+,.2f}/t ({percentage})"
    )


def _crushing(result: SimulationResult, region: Region) -> None:
    """Chart executed crushing irrespective of the selected product."""
    st.subheader("Monthly Crushing")
    series = _crushing_series(result, region)
    if all(value == 0 for _, value in series):
        st.info("No soybean crushing was executed in the selected region.")
        return
    frame = pd.DataFrame(series, columns=["Month", "Soybean Crushing (t)"]).set_index(
        "Month"
    )
    st.bar_chart(frame, y_label="Soybean Crushing (t)", x_label="Month")
    if any(value is None for _, value in series):
        st.caption("Some crushing records are unavailable.")


def _trade(result: SimulationResult, region: Region, commodity: Commodity) -> None:
    """Display executed imports, exports, and their net for the selected market."""
    st.subheader("Interregional Trade")
    st.caption("Executed trades between modeled regions; external flows are excluded.")
    series = _trade_series(result, region, commodity)
    incomplete = any(
        incoming is None or outgoing is None for _, incoming, outgoing in series
    )
    if not incomplete:
        imports = math.fsum(
            incoming for _, incoming, _ in series if incoming is not None
        )
        exports = math.fsum(
            outgoing for _, _, outgoing in series if outgoing is not None
        )
        st.caption(
            f"Imports: {imports:,.2f} t · Exports: {exports:,.2f} t · "
            f"Net imports: {imports - exports:,.2f} t"
        )
        if imports == exports == 0:
            st.info("No interregional trades were executed for this selection.")
            return
    frame = pd.DataFrame(
        series, columns=["Month", "Imports (t)", "Exports (t)"]
    ).set_index("Month")
    st.bar_chart(frame, y_label="Trade (t)", x_label="Month", stack=False)
    frame["Net imports (t)"] = frame["Imports (t)"] - frame["Exports (t)"]
    with st.expander("Monthly trade details"):
        st.dataframe(frame, use_container_width=True)
    if incomplete:
        st.info("Trade totals are unavailable because some records are invalid.")


def render(result: SimulationResult | None) -> None:
    """Render market observations from an already completed simulation.

    Args:
        result: Shared simulation result, or None before loading.
    """
    st.header("Market Analytics")
    if result is None:
        st.info("Load demo results from the sidebar to explore market analytics.")
        return
    st.success("Simulation baseline loaded.")
    months = sorted(result.monthly_results, key=lambda item: item.month)
    regions, commodities = _coverage(result)
    st.caption(
        f"{months[0].month:%Y-%m} to {months[-1].month:%Y-%m} · "
        f"{len(months)} months · {len(regions)} regions"
    )
    if not regions or not commodities:
        st.info("No regional market observations are available in this result.")
        return
    columns = st.columns(2)
    with columns[0]:
        region = _selector("Region", regions, "market_analytics_region")
    with columns[1]:
        commodity = _selector("Commodity", commodities, "market_analytics_commodity")
    assert isinstance(region, Region) and isinstance(commodity, Commodity)
    _prices(result, region, commodity)
    _crushing(result, region)
    _line_section(
        "Inventory Evolution",
        _inventory_series(result, region, commodity),
        "Ending inventory (t)",
    )
    _trade(result, region, commodity)
