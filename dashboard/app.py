"""Run the Streamlit entry point and share demo results between sections."""

import logging

import streamlit as st

from dashboard import data
from dashboard.components import market_analytics, overview, scenario_comparison


def main() -> None:
    """Navigate independent sections and load a single session-local demo on request.

    Returns:
        None: Writes the dashboard and retains successful results in session state.
    """
    st.set_page_config(page_title="Soybean Market Simulator", layout="wide")
    st.title("Soybean Market Simulator")
    st.write("Explore the foundation for monthly soybean market scenarios.")
    st.info("Synthetic demonstration data only. Results are not a forecast.")

    if "demo_comparison" not in st.session_state:
        st.session_state.demo_comparison = None
    if st.button("Load demo results"):
        st.session_state.demo_comparison = None
        try:
            with st.spinner("Loading synthetic demonstration..."):
                st.session_state.demo_comparison = data.load_demo_comparison()
        except Exception:
            # The UI remains usable; the server log retains the full traceback.
            logging.exception("Synthetic demonstration failed")
            st.error("Could not load demo results. Please try again.")

    comparison = st.session_state.demo_comparison
    baseline = None if comparison is None else comparison.baseline
    section = st.radio(
        "Section",
        ("Overview", "Market Analytics", "Scenario Comparison"),
        horizontal=True,
    )
    if section == "Overview":
        overview.render(baseline)
    elif section == "Market Analytics":
        market_analytics.render(baseline)
    else:
        scenario_comparison.render(comparison)


if __name__ == "__main__":
    main()
