"""Adapt the existing synthetic demonstration to dashboard result types."""

from scripts.compare_scenarios import create_demo, run_demo
from soybean_sim.scenarios import ScenarioComparison
from soybean_sim.simulation import SimulationResult


def load_demo_comparison() -> ScenarioComparison:
    """Run the existing synthetic baseline and drought demonstration.

    Returns:
        ScenarioComparison: Deterministic paired results from the repository demo.
    """
    return run_demo(create_demo())


def load_demo_baseline() -> SimulationResult:
    """Provide the baseline from the same synthetic comparison setup.

    Returns:
        SimulationResult: Baseline result with the demo's original inputs.
    """
    return load_demo_comparison().baseline
