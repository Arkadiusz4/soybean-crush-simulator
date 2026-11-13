"""Prepare scenario-adjusted monthly inputs independently of simulation execution."""

from ._drought import ArgentinaDroughtConfig, ScenarioError, apply_argentina_drought

__all__ = ["ArgentinaDroughtConfig", "ScenarioError", "apply_argentina_drought"]
