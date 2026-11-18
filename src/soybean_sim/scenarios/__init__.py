"""Prepare historical inputs and execute controlled baseline/drought comparisons."""

from ._comparison import (
    ScenarioComparison,
    compare_simulation_results,
    run_argentina_drought_comparison,
    run_production_shock_comparison,
)
from ._crusher_parameters import (
    CrusherParameterShockConfig,
    apply_crusher_parameter_shock,
)
from ._drought import (
    ArgentinaDroughtConfig,
    ConsumptionShockConfig,
    ProductionShockConfig,
    ScenarioError,
    apply_argentina_drought,
    apply_consumption_shock,
    apply_production_shock,
)
from ._historical import (
    HistoricalMarketError,
    HistoricalMarketInputs,
    HistoricalMetadata,
    HistoricalPreflight,
    load_historical_market,
    prepare_historical_market,
)
from ._historical_capacity import calibrate_historical_crushers
from ._historical_market import (
    compare_historical_market,
    preflight_historical_market,
    run_historical_market,
)
from ._metrics import MetricComparison, MonthlyComparison, RegionalMarketComparison

__all__ = [
    "ArgentinaDroughtConfig",
    "ConsumptionShockConfig",
    "CrusherParameterShockConfig",
    "HistoricalMarketError",
    "HistoricalMarketInputs",
    "HistoricalMetadata",
    "HistoricalPreflight",
    "MetricComparison",
    "MonthlyComparison",
    "ProductionShockConfig",
    "RegionalMarketComparison",
    "ScenarioComparison",
    "ScenarioError",
    "apply_argentina_drought",
    "apply_consumption_shock",
    "apply_crusher_parameter_shock",
    "apply_production_shock",
    "calibrate_historical_crushers",
    "compare_historical_market",
    "compare_simulation_results",
    "load_historical_market",
    "preflight_historical_market",
    "prepare_historical_market",
    "run_argentina_drought_comparison",
    "run_historical_market",
    "run_production_shock_comparison",
]
