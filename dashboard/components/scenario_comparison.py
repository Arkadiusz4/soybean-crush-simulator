"""Reserve the comparison section for baseline and drought analysis."""

import streamlit as st

from soybean_sim.scenarios import ScenarioComparison


def render(comparison: ScenarioComparison | None) -> None:
    """Display the comparison placeholder and shared-result availability.

    Args:
        comparison (ScenarioComparison | None): Synthetic paired results, if loaded.

    Returns:
        None: Writes the section to the current Streamlit container.
    """
    st.header("Scenario Comparison")
    st.write("Baseline and Argentina drought comparisons will appear here.")
    if comparison is None:
        st.info("Load demo results to make the synthetic comparison available.")
    else:
        st.success("Synthetic baseline and drought results loaded.")
