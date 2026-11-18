"""Present paired baseline and drought results without running a simulation."""

import math
from collections.abc import Iterable
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from soybean_sim.market import MarketFlowType
from soybean_sim.models import Commodity, Region
from soybean_sim.scenarios import (
    ConsumptionShockConfig,
    MetricComparison,
    MonthlyComparison,
    ProductionShockConfig,
    RegionalMarketComparison,
    ScenarioComparison,
    ScenarioError,
)
from soybean_sim.simulation import SimulationResult


def _sum_pairs(pairs: Iterable[MetricComparison]) -> MetricComparison:
    """Sum recorded baseline and drought metrics in their original units."""
    values = tuple(pairs)
    return MetricComparison(
        baseline=math.fsum(value.baseline for value in values),
        drought=math.fsum(value.drought for value in values),
    )


def _production(
    result: SimulationResult,
    region: Region = Region.ARGENTINA,
    commodity: Commodity = Commodity.SOYBEANS,
    flow_type: MarketFlowType = MarketFlowType.SUPPLY,
) -> float | None:
    """Sum recorded regional soybean supply, or return None if unrecorded."""
    quantities = []
    for month in result.monthly_results:
        observations = [
            flow.quantity_tonnes
            for flow in month.market_flows
            if flow.region == region
            and flow.commodity == commodity
            and flow.flow_type == flow_type
        ]
        if not observations:
            return None
        quantities.extend(observations)
    return math.fsum(quantities)


def _production_comparison(comparison: ScenarioComparison) -> MetricComparison | None:
    """Compare recorded production without substituting missing flows with zero."""
    region = (
        comparison.drought_config.region
        if isinstance(comparison.drought_config, ProductionShockConfig)
        else Region.ARGENTINA
    )
    config = comparison.drought_config
    commodity = (
        config.commodity
        if isinstance(config, ConsumptionShockConfig)
        else Commodity.SOYBEANS
    )
    flow_type = (
        MarketFlowType.CONSUMPTION
        if isinstance(config, ConsumptionShockConfig)
        else MarketFlowType.SUPPLY
    )
    baseline, drought = (
        _production(comparison.baseline, region, commodity, flow_type),
        _production(comparison.drought, region, commodity, flow_type),
    )
    if baseline is None or drought is None:
        return None
    return MetricComparison(baseline=baseline, drought=drought)


def _coverage(months: tuple[MonthlyComparison, ...]) -> set[tuple[Region, Commodity]]:
    """Find markets present in every paired month."""
    keys = [
        {(item.region, item.commodity) for item in month.markets} for month in months
    ]
    return set.intersection(*keys) if keys else set()


def _markets(
    months: tuple[MonthlyComparison, ...], region: Region, commodity: Commodity
) -> list[tuple[date, RegionalMarketComparison]]:
    """Select the same regional market from each observed simulation month."""
    return [
        (
            month.month,
            next(
                item
                for item in month.markets
                if item.region == region and item.commodity == commodity
            ),
        )
        for month in sorted(months, key=lambda value: value.month)
    ]


def _crushing(
    months: tuple[MonthlyComparison, ...], region: Region
) -> list[tuple[date, MetricComparison]]:
    """Select each month's executed soybean crushing for a region."""
    return [
        (
            month.month,
            next(value for key, value in month.crushing_tonnes if key == region),
        )
        for month in sorted(months, key=lambda value: value.month)
    ]


def _change(value: float, unit: str) -> str:
    """Format a signed difference without displaying rounded negative zero."""
    magnitude = f"{abs(value):,.2f}"
    sign = "" if round(value, 2) == 0 else "+" if value > 0 else "\N{MINUS SIGN}"
    separator = "" if unit == "%" else " "
    return f"{sign}{magnitude}{separator}{unit}"


def _percent(value: MetricComparison) -> str:
    """Format a signed percentage, retaining an undefined zero baseline."""
    percentage = value.percentage_difference
    return "Unavailable" if percentage is None else _change(percentage, "%")


def _pair_caption(value: MetricComparison, unit: str) -> str:
    """Describe a paired total and its signed drought-minus-baseline change."""
    return (
        f"Baseline {value.baseline:,.2f} {unit} | "
        f"Modified Scenario {value.drought:,.2f} {unit}"
        f" | Difference {_change(value.difference, unit)}"
    )


def _card(label: str, value: str, pair: MetricComparison | None, unit: str) -> None:
    """Render a neutral metric and explain its paired values."""
    with st.container(border=True):
        st.metric(label, value)
        if pair is None:
            st.caption("Recorded supply unavailable")
        else:
            st.caption(f"Baseline: {pair.baseline:,.2f} {unit}")
            st.caption(f"Modified Scenario: {pair.drought:,.2f} {unit}")


def _drought_period(start: date | None, end: date | None) -> str:
    """Describe configured inclusive bounds without implying they were clipped."""
    if start is not None and end is not None:
        return f"{start:%Y-%m} to {end:%Y-%m}"
    if start is not None:
        return f"From {start:%Y-%m}"
    if end is not None:
        return f"Through {end:%Y-%m}"
    return "Entire simulation"


def _month_format(months: Iterable[date]) -> str:
    """Include a year in chart ticks when the observations span multiple years."""
    return "%b %Y" if len({month.year for month in months}) > 1 else "%b"


def _chart(
    values: list[tuple[date, MetricComparison]], unit: str, *, bars: bool
) -> None:
    """Render paired monthly values on a chronological, clearly labeled axis."""
    frame = pd.DataFrame(
        [(month, item.baseline, item.drought) for month, item in values],
        columns=["Month", "Baseline", "Modified Scenario"],
    ).melt(id_vars="Month", var_name="Scenario", value_name="Value")
    base = alt.Chart(frame).encode(
        x=alt.X(
            "Month:T",
            title="Month",
            axis=alt.Axis(format=_month_format(m for m, _ in values)),
        ),
        y=alt.Y("Value:Q", title=unit),
        color=alt.Color("Scenario:N", title="Scenario"),
        tooltip=["Month:T", "Scenario:N", alt.Tooltip("Value:Q", format=",.2f")],
    )
    chart = (
        base.mark_bar().encode(xOffset="Scenario:N")
        if bars
        else base.mark_line(point=True)
    )
    st.altair_chart(chart, use_container_width=True)


def _label(value: Region | Commodity) -> str:
    """Format a regional or commodity identifier for display."""
    return value.value.replace("_", " ").title()


def _select(
    label: str,
    values: list[Region] | list[Commodity],
    default: Region | Commodity,
    key: str,
) -> Region | Commodity:
    """Use a stable selector and discard selections absent from new coverage."""
    if key in st.session_state and st.session_state[key] not in values:
        del st.session_state[key]
    index = next((index for index, value in enumerate(values) if value == default), 0)
    return st.selectbox(label, values, index=index, format_func=_label, key=key)


def _line_chart(
    title: str, values: list[tuple[date, MetricComparison]], unit: str
) -> None:
    """Chart paired monthly observations with a shared time axis."""
    st.subheader(title)
    _chart(values, unit, bars=False)


def _prices(markets: list[tuple[date, RegionalMarketComparison]]) -> None:
    """Plot regional prices observed during, rather than after, each month."""
    values = [(month, market.price_usd_per_tonne) for month, market in markets]
    _line_chart(
        "Baseline vs Modified Scenario Price Trends", values, "Observed price (USD/t)"
    )
    st.caption("Prices are observations during each simulated month.")
    st.caption(f"First: {_pair_caption(values[0][1], 'USD/t')}")
    st.caption(f"Last: {_pair_caption(values[-1][1], 'USD/t')}")


def _crush_chart(values: list[tuple[date, MetricComparison]]) -> None:
    """Show regional soybean crushing, including zero months in active periods."""
    st.subheader("Crushing Comparison")
    total = _sum_pairs(value for _, value in values)
    st.caption(_pair_caption(total, "t"))
    if all(value.baseline == value.drought == 0 for _, value in values):
        st.info("No soybean crushing was executed in either scenario for this region.")
        return
    _chart(values, "Soybean crushing (t)", bars=True)


def _inventories(markets: list[tuple[date, RegionalMarketComparison]]) -> None:
    """Plot paired month-end inventories, retaining valid zero stocks."""
    values = [(month, market.inventory_tonnes) for month, market in markets]
    _line_chart("Inventory Comparison", values, "Ending inventory (t)")
    st.caption(f"Final inventory: {_pair_caption(values[-1][1], 't')}")


def _trade(markets: list[tuple[date, RegionalMarketComparison]]) -> None:
    """Summarize regional executed imports, exports, and net trade."""
    st.subheader("Trade Comparison")
    st.caption("Regional totals include external boundary transfers when recorded.")
    imports = _sum_pairs(item.imports_tonnes for _, item in markets)
    exports = _sum_pairs(item.exports_tonnes for _, item in markets)
    net = _sum_pairs(item.net_trade_tonnes for _, item in markets)
    st.caption(f"Imports: {_pair_caption(imports, 't')}")
    st.caption(f"Exports: {_pair_caption(exports, 't')}")
    st.caption(f"Net imports: {_pair_caption(net, 't')}")
    if all(
        value == 0
        for pair in (imports, exports)
        for value in (pair.baseline, pair.drought)
    ):
        st.info("No trades were executed for this market in either scenario.")
        return
    if all(
        pair.baseline == pair.drought
        for _, item in markets
        for pair in (item.imports_tonnes, item.exports_tonnes)
    ):
        st.info("The current results contain no difference in executed trade.")
    rows = [
        {
            "Month": month,
            "Baseline imports (t)": item.imports_tonnes.baseline,
            "Modified imports (t)": item.imports_tonnes.drought,
            "Baseline exports (t)": item.exports_tonnes.baseline,
            "Modified exports (t)": item.exports_tonnes.drought,
            "Baseline net (t)": item.net_trade_tonnes.baseline,
            "Modified net (t)": item.net_trade_tonnes.drought,
            "Net change": _change(item.net_trade_tonnes.difference, "t"),
        }
        for month, item in markets
    ]
    with st.expander("Monthly trade details"):
        st.dataframe(pd.DataFrame(rows).set_index("Month"), use_container_width=True)


def render(comparison: ScenarioComparison | None) -> None:
    """Display a completed comparison or a prompt to load demo results.

    Args:
        comparison: Paired results supplied by the application, or None.
    """
    st.header("Scenario Comparison")
    if comparison is None:
        st.info(
            "Comparison results are not loaded. Use 'Run Simulation' "
            "in the sidebar to load a comparison."
        )
        return
    try:
        months = tuple(
            sorted(comparison.monthly_metrics(), key=lambda month: month.month)
        )
        production = _production_comparison(comparison)
        total_crushing = _sum_pairs(
            value for month in months for _, value in month.crushing_tonnes
        )
        total_trade = _sum_pairs(month.trade_volume_tonnes for month in months)
        coverage = _coverage(months)
    except (ScenarioError, ValueError, OverflowError) as exc:
        st.error(f"Cannot display invalid comparison data: {exc}")
        return
    if not months or not coverage:
        st.error("Comparison data has no complete regional market coverage.")
        return
    st.success("Baseline and modified scenario results loaded.")
    st.subheader("Baseline vs Shock")
    config = comparison.drought_config
    shock_region = (
        config.region if isinstance(config, ProductionShockConfig) else Region.ARGENTINA
    )
    change = (
        config.percentage_change
        if isinstance(config, ProductionShockConfig)
        else -100 * config.production_reduction_fraction
    )
    commodity_label = (
        _label(config.commodity)
        if isinstance(config, ConsumptionShockConfig)
        else "Soybean"
    )
    shock_label = (
        "consumption" if isinstance(config, ConsumptionShockConfig) else "production"
    )
    period = _drought_period(config.start_month, config.end_month)
    st.caption(
        f"{months[0].month:%Y-%m} to {months[-1].month:%Y-%m} · "
        f"{len(months)} months · {_label(shock_region)} "
        f"{commodity_label.lower()} {shock_label} "
        f"{_change(change, '%')} · Shock period: {period} (inclusive)"
    )
    st.caption("Simulated results are not forecasts.")
    regions = sorted({region for region, _ in coverage})
    filter_columns = st.columns(2)
    with filter_columns[0]:
        region = _select(
            "Region", regions, Region.ARGENTINA, "scenario_comparison_region"
        )
    assert isinstance(region, Region)
    commodities = sorted({commodity for key, commodity in coverage if key == region})
    with filter_columns[1]:
        commodity = _select(
            "Commodity",
            commodities,
            Commodity.SOYBEANS,
            "scenario_comparison_commodity",
        )
    assert isinstance(commodity, Commodity)
    markets = _markets(months, region, commodity)
    try:
        crushing = _crushing(months, region)
        cards = [*st.columns(2), *st.columns(2)]
        with cards[0]:
            st.caption(
                f"{_label(shock_region)} {commodity_label.lower()} "
                f"requested {shock_label}"
            )
            _card(
                "Consumption Shock"
                if isinstance(config, ConsumptionShockConfig)
                else "Production Shock",
                "Unavailable" if production is None else _percent(production),
                production,
                "t",
            )
            if production is not None:
                st.caption(f"Difference: {_change(production.difference, 't')}")
            if isinstance(config, ConsumptionShockConfig):
                totals = []
                for result in (comparison.baseline, comparison.drought):
                    records = [
                        next(
                            (
                                item
                                for item in (month.consumption_results or ())
                                if item.region == config.region
                                and item.commodity == config.commodity
                            ),
                            None,
                        )
                        for month in result.monthly_results
                    ]
                    totals.append(
                        None
                        if any(item is None for item in records)
                        else (
                            math.fsum(
                                item.fulfilled_tonnes
                                for item in records
                                if item is not None
                            ),
                            math.fsum(
                                item.unmet_tonnes
                                for item in records
                                if item is not None
                            ),
                        )
                    )
                if any(item is None for item in totals):
                    st.caption(
                        "Fulfilled and unmet consumption are not recorded for this run."
                    )
                else:
                    for label, item in zip(
                        ("Baseline", "Modified Scenario"), totals, strict=True
                    ):
                        assert item is not None
                        st.caption(
                            f"{label}: fulfilled {item[0]:,.2f} t; "
                            f"unmet {item[1]:,.2f} t"
                        )
        with cards[1]:
            _card(
                "Crushing Impact",
                _change(total_crushing.difference, "t"),
                total_crushing,
                "t",
            )
        with cards[2]:
            _card(
                "Trade Impact",
                _change(total_trade.difference, "t"),
                total_trade,
                "t",
            )
        with cards[3]:
            price = markets[-1][1].price_usd_per_tonne
            _card("Price Impact", _percent(price), price, "USD/t")
        _prices(markets)
        _crush_chart(crushing)
        _inventories(markets)
        _trade(markets)
    except (ValueError, OverflowError, StopIteration) as exc:
        st.error(f"Cannot display invalid comparison data: {exc}")
