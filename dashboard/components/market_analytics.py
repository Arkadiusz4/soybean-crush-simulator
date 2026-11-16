"""Reserve market analytics for regional market visualizations."""

import streamlit as st

from soybean_sim.simulation import SimulationResult


def render(result: SimulationResult | None) -> None:
    """Display the analytics placeholder and its shared-result availability.

    Args:
        result (SimulationResult | None): Shared synthetic baseline, if loaded.

    Returns:
        None: Writes the section to the current Streamlit container.
    """
    st.header("Market Analytics")
    st.write("Regional prices, inventories, crushing, and trade will appear here.")
    if result is None:
        st.info("Load demo results to make the synthetic baseline available.")
    else:
        st.success("Synthetic baseline loaded.")
