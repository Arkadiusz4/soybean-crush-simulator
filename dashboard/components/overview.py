"""Summarize executed market activity and final stocks from a simulation result."""

import math
from collections import defaultdict

import streamlit as st

from soybean_sim.agents import DecisionAction
from soybean_sim.market import SettlementOutcome
from soybean_sim.models import Commodity, Region
from soybean_sim.simulation import SimulationResult


def _compact(value: float) -> str:
    """Format large quantities compactly while keeping smaller values readable."""
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(value) >= threshold:
            return f"{value / threshold:,.2f}".rstrip("0").rstrip(".") + suffix
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _activity(
    result: SimulationResult,
) -> tuple[
    dict[Region, list[float]], dict[Region, list[float]], dict[Region, list[float]], int
]:
    """Collect executed regional crushing and trade without counting intentions.

    Args:
        result (SimulationResult): Completed monthly simulation history.

    Returns:
        tuple: Regional crushing, imports, exports, and interregional trade count.
    """
    crushing: dict[Region, list[float]] = defaultdict(list)
    imports: dict[Region, list[float]] = defaultdict(list)
    exports: dict[Region, list[float]] = defaultdict(list)
    trade_count = 0
    for month in result.monthly_results:
        for settlement in month.settlement_results:
            if (
                settlement.decision.action == DecisionAction.CRUSH
                and settlement.outcome == SettlementOutcome.CRUSH_SETTLED
            ):
                crushing[settlement.decision.agent.region].append(
                    settlement.executed_tonnes
                )
        for trade in month.executed_trades:
            imports[trade.destination_region].append(trade.quantity_tonnes)
            exports[trade.source_region].append(trade.quantity_tonnes)
            trade_count += 1
        for trade in month.external_trade_flows:
            (imports if trade.is_import else exports)[trade.region].append(
                trade.quantity_tonnes
            )
    return crushing, imports, exports, trade_count


def render(result: SimulationResult | None) -> None:
    """Display physical activity and final stocks from the supplied result.

    Args:
        result (SimulationResult | None): Completed shared result, if loaded.

    Returns:
        None: Writes the overview without changing the result.
    """
    st.header("Market Overview")
    if result is None:
        st.info(
            "No results are loaded. Use 'Run Simulation' in the sidebar to get started."
        )
        return

    st.success("Simulation results loaded.")
    months = result.monthly_results
    stocks = {
        (item.region, item.commodity): item.quantity_tonnes
        for item in result.final_state.snapshot.inventories
    }
    crushing, imports, exports, trade_count = _activity(result)
    regions = sorted(
        {item.region for item in result.initial_state.snapshot.inventories}
        | {item.region for item in result.final_state.snapshot.inventories}
        | crushing.keys()
        | imports.keys()
        | exports.keys()
    )
    region_names = [region.value.replace("_", " ").title() for region in regions]
    st.write(
        f"Period: {months[0].month:%Y-%m} to {months[-1].month:%Y-%m} · "
        f"{len(months)} months · {len(regions)} regions: {', '.join(region_names)}"
    )
    sources = sorted(
        {price.data_source for price in result.initial_state.snapshot.prices}
    )
    st.caption(f"Reference price sources: {', '.join(sources)}")

    total_crushing = math.fsum(
        value for quantities in crushing.values() for value in quantities
    )
    total_trade = math.fsum(
        trade.quantity_tonnes for month in months for trade in month.executed_trades
    )
    cards = st.columns(4)
    for card, label, value in zip(
        cards,
        (
            "Executed crushing (t)",
            "Interregional trade (t)",
            "Executed trades",
            "Simulated months",
        ),
        (
            _compact(total_crushing),
            _compact(total_trade),
            f"{trade_count:,}",
            f"{len(months):,}",
        ),
        strict=True,
    ):
        with card.container(border=True):
            exact = {
                "Executed crushing (t)": total_crushing,
                "Interregional trade (t)": total_trade,
            }.get(label)
            st.metric(
                label, value, help=None if exact is None else f"Exact: {exact:,.2f} t"
            )

    regional_rows = []
    for region, name in zip(regions, region_names, strict=True):
        soybeans = stocks.get((region, Commodity.SOYBEANS))
        regional_rows.append(
            {
                "Region": name,
                "Executed crushing (t)": f"{math.fsum(crushing.get(region, ())):,.2f}",
                "Imports (t)": f"{math.fsum(imports.get(region, ())):,.2f}",
                "Exports (t)": f"{math.fsum(exports.get(region, ())):,.2f}",
                "Final soybean inventory (t)": "Unavailable"
                if soybeans is None
                else f"{soybeans:,.2f}",
            }
        )
    st.subheader("Regional Activity")
    st.caption(
        "Imports and exports include executed external boundary transfers when present."
    )
    st.dataframe(regional_rows, hide_index=True, use_container_width=True)

    st.subheader("Final Inventories")
    inventory_cards = st.columns(3)
    for card, commodity in zip(
        inventory_cards,
        (
            Commodity.SOYBEANS,
            Commodity.SOYBEAN_MEAL,
            Commodity.SOYBEAN_OIL,
        ),
        strict=True,
    ):
        quantities = [stocks.get((region, commodity)) for region in regions]
        total = (
            _compact(math.fsum(value for value in quantities if value is not None))
            if quantities and all(value is not None for value in quantities)
            else "Unavailable"
        )
        with card.container(border=True):
            exact = math.fsum(value for value in quantities if value is not None)
            st.metric(
                f"{commodity.value.replace('_', ' ').title()} (t)",
                total,
                help=f"Exact: {exact:,.2f} t" if total != "Unavailable" else None,
            )
