"""Explore recorded monthly market activity without rerunning the simulation."""

import math
from datetime import date

import altair as alt
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


def _chart(
    frame: pd.DataFrame, unit: str, *, height: int = 400, bars: bool = False
) -> None:
    """Render existing observations with consistent month ticks and full tooltips.

    Args:
        frame (pd.DataFrame): Series columns indexed by dates named Month.
        unit (str): Vertical axis label including the quantity's units.
        height (int): Plot height in pixels, excluding the legend and axes.
        bars (bool): Use grouped bars instead of lines with explicit gaps.

    Returns:
        None: Writes a chart without modifying the source observations.
    """
    records = frame.reset_index().melt(
        id_vars="Month", var_name="Series", value_name="Value"
    )
    years = {month.year for month in frame.index}
    base = alt.Chart(records).encode(
        x=alt.X(
            "Month:T",
            title="Month",
            axis=alt.Axis(format="%b %Y" if len(years) > 1 else "%b"),
        ),
        y=alt.Y("Value:Q", title=unit),
        color=alt.Color(
            "Series:N",
            legend=alt.Legend(
                orient="bottom", direction="horizontal", labelLimit=180, columns=2
            ),
        ),
        tooltip=[
            alt.Tooltip("Month:T", format="%b %Y"),
            "Series:N",
            alt.Tooltip("Value:Q", format=",.2f"),
        ],
    )
    chart = (
        base.mark_bar().encode(xOffset="Series:N")
        if bars
        else base.mark_line(point=True, invalid="break-paths-show-domains")
    )
    # Fit only the width so legends cannot consume the requested plot height.
    chart = chart.properties(
        height=height, autosize=alt.AutoSizeParams(type="fit-x", contains="padding")
    )
    st.altair_chart(chart, width="stretch", height="content")


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
    st.line_chart(frame, y_label=value_label, x_label="Month", height=400)
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


def _indexed(
    series: list[tuple[date, float | None]],
) -> list[tuple[date, float | None]]:
    """Index observed prices to their first observation, preserving gaps."""
    first = next((value for _, value in series if value is not None), None)
    if first is None or first == 0:
        return [(month, None) for month, _ in series]
    return [
        (month, None if value is None else value / first * 100)
        for month, value in series
    ]


def _multi_chart(
    title: str,
    series: dict[str, list[tuple[date, float | None]]],
    unit: str,
    *,
    height: int = 400,
) -> None:
    """Display selected commodity observations without filling missing values.

    Args:
        title (str): Section heading.
        series (dict): Named series of monthly observations, including gaps.
        unit (str): Vertical axis label with units.
        height (int): Plot height in pixels, excluding labels and legend.

    Returns:
        None: Renders recorded values or an informative empty state.
    """
    st.subheader(title)
    if not any(value is not None for values in series.values() for _, value in values):
        st.info("No observations are available for this selection.")
        return
    frame = pd.DataFrame(
        {
            label: pd.Series(dict(values), dtype=float)
            for label, values in series.items()
        }
    ).sort_index()
    frame.index.name = "Month"
    _chart(frame, unit, height=height)
    if frame.isna().any().any():
        st.caption("Missing observations are shown as gaps.")


def _combined_trade_series(
    result: SimulationResult, region: Region, commodities: tuple[Commodity, ...]
) -> list[tuple[date, float | None, float | None]]:
    """Combine distinct selected commodities without counting trades twice."""
    series = [
        _trade_series(result, region, commodity) for commodity in set(commodities)
    ]
    combined = []
    for rows in zip(*series, strict=True):
        incoming = [row[1] for row in rows]
        outgoing = [row[2] for row in rows]
        combined.append(
            (
                rows[0][0],
                math.fsum(value for value in incoming if value is not None)
                if all(value is not None for value in incoming)
                else None,
                math.fsum(value for value in outgoing if value is not None)
                if all(value is not None for value in outgoing)
                else None,
            )
        )
    return combined


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
    st.bar_chart(frame, y_label="Soybean Crushing (t)", x_label="Month", height=320)
    if any(value is None for _, value in series):
        st.caption("Some crushing records are unavailable.")


def _crushing_regions(result: SimulationResult, regions: list[Region]) -> None:
    """Compare actual crushing by region, independent of commodity filters."""
    st.subheader("Monthly Crushing")
    series = {_label(region): _crushing_series(result, region) for region in regions}
    if all(value == 0 for values in series.values() for _, value in values):
        st.info("No soybean crushing was executed in the selected regions.")
        return
    frame = pd.DataFrame(
        {name: pd.Series(dict(values), dtype=float) for name, values in series.items()}
    ).sort_index()
    frame.index.name = "Month"
    _chart(frame, "Soybean crushing (t)", height=320, bars=True)
    if frame.isna().any().any():
        st.caption("Some crushing records are unavailable.")


def _regional_trade_series(
    result: SimulationResult, regions: list[Region], commodities: list[Commodity]
) -> list[tuple[date, float | None, float | None, float | None]]:
    """Count directional regional activity and each involved physical trade once."""
    selected_regions, selected_commodities = set(regions), set(commodities)
    output = []
    for month in sorted(result.monthly_results, key=lambda item: item.month):
        trades = [
            trade
            for trade in month.executed_trades
            if trade.commodity in selected_commodities
            and (
                trade.source_region in selected_regions
                or trade.destination_region in selected_regions
            )
        ]
        if not all(math.isfinite(trade.quantity_tonnes) for trade in trades):
            output.append((month.month, None, None, None))
            continue
        imports = math.fsum(
            trade.quantity_tonnes
            for trade in trades
            if trade.destination_region in selected_regions
        )
        exports = math.fsum(
            trade.quantity_tonnes
            for trade in trades
            if trade.source_region in selected_regions
        )
        unique = math.fsum(trade.quantity_tonnes for trade in trades)
        output.append((month.month, imports, exports, unique))
    return output


def _regional_trade(
    result: SimulationResult, regions: list[Region], commodities: list[Commodity]
) -> None:
    """Show aggregate directional activity and unique involved trade volume."""
    st.subheader("Interregional Trade")
    st.caption(
        "Selected-region imports and exports include transfers between selected "
        "regions. External flows are excluded."
    )
    series = _regional_trade_series(result, regions, commodities)
    if any(value is None for row in series for value in row[1:]):
        st.info("Trade totals are unavailable because some records are invalid.")
        return
    imports = math.fsum(row[1] for row in series if row[1] is not None)
    exports = math.fsum(row[2] for row in series if row[2] is not None)
    volume = math.fsum(row[3] for row in series if row[3] is not None)
    st.caption(
        f"Regional imports: {imports:,.2f} t · Regional exports: {exports:,.2f} t · "
        f"Unique involved trade: {volume:,.2f} t"
    )
    if imports == exports == 0:
        st.info("No interregional trades were executed for this selection.")
        return
    frame = pd.DataFrame(
        series,
        columns=[
            "Month",
            "Regional imports (t)",
            "Regional exports (t)",
            "Unique involved trade (t)",
        ],
    ).set_index("Month")
    _chart(
        frame.filter(items=["Regional imports (t)", "Regional exports (t)"]),
        "Trade (t)",
        height=320,
        bars=True,
    )
    with st.expander("Monthly trade details"):
        st.dataframe(frame, use_container_width=True)


def _trade(
    result: SimulationResult,
    region: Region,
    commodity: Commodity | tuple[Commodity, ...],
) -> None:
    """Display executed imports, exports, and their net for the selected market."""
    st.subheader("Interregional Trade")
    st.caption(
        "Combined selected commodities between modeled regions; "
        "external flows are excluded."
    )
    series = (
        _trade_series(result, region, commodity)
        if isinstance(commodity, Commodity)
        else _combined_trade_series(result, region, commodity)
    )
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
    st.bar_chart(frame, y_label="Trade (t)", x_label="Month", height=320, stack=False)
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
        st.info("Use Run Simulation in the sidebar to explore market analytics.")
        return
    st.success("Simulation results loaded.")
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
        region_key = "market_analytics_regions"
        if region_key in st.session_state:
            st.session_state[region_key] = [
                item for item in st.session_state[region_key] if item in regions
            ]
        selected_regions = st.multiselect(
            "Regions",
            regions,
            default=[Region.ARGENTINA] if Region.ARGENTINA in regions else regions[:1],
            format_func=_label,
            key=region_key,
        )
    with columns[1]:
        key = "market_analytics_commodities"
        if key in st.session_state:
            st.session_state[key] = [
                item for item in st.session_state[key] if item in commodities
            ]
        selected = st.multiselect(
            "Commodities", commodities, default=commodities, format_func=_label, key=key
        )
    if not selected_regions:
        st.info("Select at least one region to view market activity.")
        return
    if selected:
        mode = st.radio(
            "Price display",
            ("Absolute prices", "Indexed (first observation = 100)"),
            horizontal=True,
        )
        prices = {
            f"{_label(region)} / {_label(commodity)}": _price_series(
                result, region, commodity
            )
            for region in selected_regions
            for commodity in selected
        }
        if len(prices) > 6:
            st.caption(
                "Many series are selected; narrow the filters for a clearer chart."
            )
        if mode.startswith("Indexed"):
            prices = {label: _indexed(series) for label, series in prices.items()}
            if any(
                all(value is None for _, value in series) for series in prices.values()
            ):
                st.info(
                    "An index is unavailable when its first price is zero or missing."
                )
        _multi_chart(
            "Regional Price Trends",
            prices,
            "Price index (first observation = 100)"
            if mode.startswith("Indexed")
            else "Observed price (USD/t)",
        )
    else:
        st.info("Select at least one commodity to view prices, inventories, and trade.")
    _crushing_regions(result, selected_regions)
    if selected:
        st.caption(
            "Inventory lines show tonnes. Narrow the filters if stock scales "
            "differ substantially."
        )
        _multi_chart(
            "Inventory Evolution",
            {
                f"{_label(region)} / {_label(commodity)}": _inventory_series(
                    result, region, commodity
                )
                for region in selected_regions
                for commodity in selected
            },
            "Ending inventory (t)",
            height=350,
        )
        _regional_trade(result, selected_regions, selected)
