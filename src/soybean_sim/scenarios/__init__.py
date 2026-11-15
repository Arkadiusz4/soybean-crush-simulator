"""Prepare drought inputs and compare controlled baseline/drought executions."""

from ._comparison import (
    ScenarioComparison,
    compare_simulation_results,
    run_argentina_drought_comparison,
)
from ._drought import ArgentinaDroughtConfig, ScenarioError, apply_argentina_drought
from ._metrics import MetricComparison, MonthlyComparison, RegionalMarketComparison

__all__ = [
    "ArgentinaDroughtConfig",
    "MetricComparison",
    "MonthlyComparison",
    "RegionalMarketComparison",
    "ScenarioComparison",
    "ScenarioError",
    "apply_argentina_drought",
    "compare_simulation_results",
    "run_argentina_drought_comparison",
]
