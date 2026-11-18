"""Run the Streamlit dashboard with explicitly requested simulation results."""

import logging
from datetime import date

import streamlit as st

from dashboard import data
from dashboard.components import market_analytics, overview, scenario_comparison


def main() -> None:
    """Display controls and retain the most recently successful run."""
    st.set_page_config(page_title="Soybean Market Simulator", layout="centered")
    st.title("Soybean Market Simulator")
    if "dashboard_result" not in st.session_state:
        st.session_state.dashboard_result = None
    current = st.session_state.dashboard_result
    outdated_result = current is not None and not all(
        hasattr(current.config, field)
        for field in (
            "price_sensitivity",
            "maximum_monthly_change",
            "drought_start",
            "drought_end",
            "year",
        )
    )
    if outdated_result:
        st.session_state.dashboard_result = None
        st.warning(
            "Saved results use an older configuration. Please run the simulation again."
        )
    with st.sidebar:
        st.header("Dashboard")
        section = st.radio(
            "Section", ("Overview", "Market Analytics", "Scenario Comparison")
        )
        source = st.selectbox(
            "Data source", ("Synthetic", "Historical USDA/World Bank")
        )
        if source == "Historical USDA/World Bank":
            try:
                years = data.supported_historical_years()
            except (ValueError, OSError) as exc:
                st.error(f"Could not inspect historical data: {exc}")
                years = ()
            if years:
                year = st.selectbox(
                    "Simulation Year",
                    years,
                    index=years.index(2025) if 2025 in years else 0,
                )
            else:
                year = None
                st.warning("No supported historical years have complete local data.")
        else:
            year = 2025
            st.caption("Synthetic simulation period: 2025.")
        scenario = st.selectbox("Market Scenario", ("Baseline", "Argentina Drought"))
        output = st.selectbox(
            "Output",
            ("Single Simulation", "Compare with Baseline")
            if scenario == "Argentina Drought"
            else ("Single Simulation",),
        )
        mode = (
            "Baseline"
            if scenario == "Baseline"
            else "Baseline vs Drought"
            if output == "Compare with Baseline"
            else "Argentina Drought"
        )
        reduction = (
            st.slider("Drought production reduction (%)", 0, 100, 30)
            if scenario == "Argentina Drought"
            else 30
        )
        defaults = data.PriceDynamicsConfig()
        with st.expander("Advanced Settings"):
            sensitivity = st.number_input(
                "Price sensitivity",
                min_value=0.0,
                max_value=10.0,
                value=defaults.price_sensitivity,
                step=0.05,
                help="Higher values increase the response to supply and demand "
                "imbalances.",
            )
            cap = st.number_input(
                "Maximum monthly price change (%)",
                min_value=0.0,
                max_value=99.9,
                value=defaults.maximum_monthly_change * 100,
                step=1.0,
                help="Limits the size of each monthly price update.",
            )
            start = end = None
            if scenario == "Argentina Drought" and year is not None:
                months = [date(year, month, 1) for month in range(1, 13)]
                start = st.selectbox(
                    "Drought start month",
                    months,
                    key=f"drought_start_{year}",
                    format_func=lambda month: month.strftime("%Y-%m"),
                )
                end = st.selectbox(
                    "Drought end month",
                    months,
                    index=11,
                    key=f"drought_end_{year}",
                    format_func=lambda month: month.strftime("%Y-%m"),
                )
                st.caption("Drought start and end months are inclusive.")
        requested = data.RunConfig(
            source, mode, reduction, sensitivity, cap / 100, start, end, year or 2025
        )
        if st.button("Run Simulation", type="primary", disabled=year is None):
            try:
                with st.spinner("Running simulation..."):
                    result = data.run_dashboard_simulation(requested)
                st.session_state.dashboard_result = result
            except Exception as exc:
                logging.exception("Simulation execution failed")
                st.error(f"Simulation failed: {exc}")
        current = st.session_state.dashboard_result
        if current is None:
            st.caption("No results loaded. Choose settings and run the simulation.")
        else:
            config = current.config
            description = (
                f"{config.source} · {config.scenario} · {config.output} · {config.year}"
            )
            if config.scenario == "Argentina Drought":
                description += f" · {config.reduction_percent}%"
            st.caption(f"Last completed run: {description}")
            st.caption(
                f"Price sensitivity: {config.price_sensitivity:g} · "
                f"Monthly cap: {config.maximum_monthly_change:.1%}"
            )
            if config.scenario == "Argentina Drought":
                st.caption(
                    f"Drought period: {config.drought_start:%Y-%m} to "
                    f"{config.drought_end:%Y-%m} (inclusive)"
                )
            if requested != config:
                st.caption(
                    "Selected settings have not been run; "
                    "showing the last completed run."
                )
            if config.source == "Historical USDA/World Bank":
                st.warning(
                    "Historical inputs initialize a modeled market. "
                    "Marketing-year opening stocks are not reconstructed "
                    "January inventories. "
                    "Demand may be unmet "
                    "and simulated prices can diverge from reference prices; "
                    "this is not a forecast."
                )
            else:
                st.caption("Synthetic results are not forecasts.")
    baseline = None if current is None else current.baseline
    comparison = None if current is None else current.comparison
    if section == "Overview":
        overview.render(baseline)
    elif section == "Market Analytics":
        market_analytics.render(baseline)
    else:
        scenario_comparison.render(comparison)


if __name__ == "__main__":
    main()
