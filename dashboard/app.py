"""Run the Streamlit dashboard with explicitly requested simulation results."""

import logging
from datetime import date

import streamlit as st

from dashboard import data
from dashboard.components import market_analytics, overview, scenario_comparison
from soybean_sim.models import Commodity, Region


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
            "shock_region",
            "shock_percent",
        )
    )
    if outdated_result:
        st.session_state.dashboard_result = None
        current = None
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
        scenario = st.selectbox(
            "Market Scenario", ("Baseline", "Production Shock", "Consumption Shock")
        )
        shock_region = Region.ARGENTINA
        shock_commodity = Commodity.SOYBEAN_MEAL
        change = -30
        if scenario != "Baseline":
            shock_region = st.selectbox(
                "Consumption region"
                if scenario == "Consumption Shock"
                else "Production region",
                list(Region),
                index=list(Region).index(Region.ARGENTINA),
                format_func=lambda region: region.value.replace("_", " ").title(),
            )
            if scenario == "Consumption Shock":
                shock_commodity = st.selectbox(
                    "Consumption commodity",
                    (Commodity.SOYBEAN_MEAL, Commodity.SOYBEAN_OIL),
                    format_func=lambda item: item.value.replace("_", " ").title(),
                )
            previous_change = (
                current.config.shock_percent
                if current is not None and current.config.shock_percent is not None
                else None
            )
            if "shock_stress_mode" not in st.session_state:
                st.session_state.shock_stress_mode = (
                    previous_change is not None and previous_change > 100
                )
            stress = st.checkbox(
                "Stress testing (up to +1000%)",
                key="shock_stress_mode",
                help="Uses a separate value from the standard slider.",
            )
            if stress:
                change = st.number_input(
                    f"Stress-test {scenario.split()[0].lower()} change (%)",
                    min_value=-100.0,
                    max_value=1000.0,
                    value=st.session_state.get(
                        "shock_stress_saved",
                        float(previous_change)
                        if previous_change is not None
                        else -30.0,
                    ),
                    step=5.0,
                )
                st.session_state.shock_stress_saved = change
            else:
                change = st.slider(
                    "Consumption change (%)"
                    if scenario == "Consumption Shock"
                    else "Soybean production change (%)",
                    -100,
                    100,
                    st.session_state.get("shock_standard_saved", -30),
                    step=5,
                )
                st.session_state.shock_standard_saved = change
                if previous_change is not None and previous_change > 100:
                    st.caption(
                        "The last run used a stress-test value; "
                        "enable stress testing to reuse it."
                    )
        assert change is not None
        output = st.selectbox(
            "Output",
            ("Single Simulation", "Compare with Baseline")
            if scenario != "Baseline"
            else ("Single Simulation",),
        )
        mode = (
            "Baseline"
            if scenario == "Baseline"
            else "Baseline vs Drought"
            if output == "Compare with Baseline"
            else scenario
        )
        reduction = -change if change <= 0 else 0
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
            if scenario != "Baseline" and year is not None:
                months = [date(year, month, 1) for month in range(1, 13)]
                start = st.selectbox(
                    "Shock start month",
                    months,
                    key=f"drought_start_{year}",
                    format_func=lambda month: month.strftime("%Y-%m"),
                )
                end = st.selectbox(
                    "Shock end month",
                    months,
                    index=11,
                    key=f"drought_end_{year}",
                    format_func=lambda month: month.strftime("%Y-%m"),
                )
                st.caption("Shock start and end months are inclusive.")
        requested = data.RunConfig(
            source,
            mode,
            reduction,
            sensitivity,
            cap / 100,
            start,
            end,
            year or 2025,
            shock_region,
            float(change) if scenario != "Baseline" else None,
            scenario if scenario != "Baseline" else "Production Shock",
            shock_commodity,
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
            if config.scenario != "Baseline":
                change_value = (
                    config.shock_percent
                    if config.shock_percent is not None
                    else -config.reduction_percent
                )
                region_label = config.shock_region.value.replace("_", " ").title()
                description += f" · {region_label} · {change_value:+g}%"
                if config.scenario == "Consumption Shock":
                    description += (
                        f" · {config.shock_commodity.value.replace('_', ' ').title()}"
                    )
            st.caption(f"Last completed run: {description}")
            st.caption(
                f"Price sensitivity: {config.price_sensitivity:g} · "
                f"Monthly cap: {config.maximum_monthly_change:.1%}"
            )
            if config.scenario != "Baseline":
                st.caption(
                    f"Shock period: {config.drought_start:%Y-%m} to "
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
    if comparison is not None and section in ("Overview", "Market Analytics"):
        st.caption("Showing the baseline run. Open Scenario Comparison for both runs.")
    if section == "Overview":
        overview.render(baseline)
    elif section == "Market Analytics":
        market_analytics.render(baseline)
    else:
        scenario_comparison.render(comparison)


if __name__ == "__main__":
    main()
