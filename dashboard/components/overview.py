"""Reserve the overview section for controls and summary indicators."""

import streamlit as st

from soybean_sim.simulation import SimulationResult


def render(result: SimulationResult | None) -> None:
    """Display the overview placeholder and its shared-result availability.

    Args:
        result (SimulationResult | None): Shared synthetic baseline, if loaded.

    Returns:
        None: Writes the section to the current Streamlit container.
    """
    st.header("Overview")
    st.write("Simulation controls and summary indicators will appear here.")
    if result is None:
        st.info("Load demo results to make the synthetic baseline available.")
    else:
        st.success("Synthetic baseline loaded.")
