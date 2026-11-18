"""Adapt the existing synthetic demonstration to dashboard result types."""

from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache
from inspect import signature
from pathlib import Path

from scripts.compare_scenarios import create_agents, create_demo, run_demo
from soybean_sim.agents import TraderConfig
from soybean_sim.data.usda import load_usda_psd_csv
from soybean_sim.data.world_bank import load_world_bank_prices
from soybean_sim.market import PriceDynamicsConfig
from soybean_sim.models import Commodity, Region
from soybean_sim.scenarios import (
    HistoricalMarketInputs,
    ProductionShockConfig,
    ScenarioComparison,
    apply_production_shock,
    calibrate_historical_crushers,
    compare_historical_market,
    load_historical_market,
    prepare_historical_market,
    run_historical_market,
    run_production_shock_comparison,
)
from soybean_sim.simulation import SimulationResult, run_simulation


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


@dataclass(frozen=True)
class RunConfig:
    """Describe the requested source and scenario settings."""

    source: str
    mode: str
    reduction_percent: float = 30
    price_sensitivity: float = PriceDynamicsConfig().price_sensitivity
    maximum_monthly_change: float = PriceDynamicsConfig().maximum_monthly_change
    drought_start: date | None = None
    drought_end: date | None = None
    year: int = 2025
    shock_region: Region = Region.ARGENTINA
    shock_percent: float | None = None

    @property
    def scenario(self) -> str:
        """Identify the configured market scenario."""
        return "Baseline" if self.mode == "Baseline" else "Production Shock"

    @property
    def output(self) -> str:
        """Identify whether the completed run contains a comparison."""
        return (
            "Compare with Baseline"
            if self.mode == "Baseline vs Drought"
            else "Single Simulation"
        )

    def __post_init__(self) -> None:
        """Normalize inactive drought controls and full-period bounds."""
        if self.mode == "Baseline":
            object.__setattr__(self, "reduction_percent", 30)
            object.__setattr__(self, "drought_start", None)
            object.__setattr__(self, "drought_end", None)
            object.__setattr__(self, "shock_region", Region.ARGENTINA)
            object.__setattr__(self, "shock_percent", None)
        else:
            object.__setattr__(
                self, "drought_start", self.drought_start or date(self.year, 1, 1)
            )
            object.__setattr__(
                self, "drought_end", self.drought_end or date(self.year, 12, 1)
            )


@dataclass(frozen=True)
class DashboardResult:
    """Keep completed results together with their execution settings."""

    config: RunConfig
    baseline: SimulationResult
    comparison: ScenarioComparison | None


def _historical_paths() -> tuple[Path, Path]:
    """Locate the locally maintained USDA and World Bank files."""
    root = Path(__file__).resolve().parents[1] / "data" / "raw"
    return root / "psd_oilseeds.csv", root / "CMO-Historical-Data-Monthly.xlsx"


def _load_year(year: int) -> HistoricalMarketInputs:
    """Select USDA year N-1 and January prices for calendar year N."""
    usda, world_bank = _historical_paths()
    january = date(year, 1, 1)
    return load_historical_market(
        usda,
        world_bank,
        marketing_year=str(year - 1),
        price_month=january,
        start_month=january,
        include_historical_trade=False,
    )


@lru_cache(maxsize=4)
def _validated_years(
    usda: Path, bank: Path, usda_time: int, bank_time: int
) -> tuple[int, ...]:
    """Validate each covered January against annual balances and calibration."""
    years = []
    balances = load_usda_psd_csv(usda)
    prices = load_world_bank_prices(bank)
    annual = {balance.marketing_year for balance in balances}
    candidates = sorted(
        {
            price.month.year
            for price in prices
            if price.month.month == 1 and str(price.month.year - 1) in annual
        }
    )
    for year in candidates:
        try:
            january = date(year, 1, 1)
            prepared = prepare_historical_market(
                (
                    balance
                    for balance in balances
                    if balance.marketing_year == str(year - 1)
                ),
                (price for price in prices if price.month == january),
                marketing_year=str(year - 1),
                price_month=january,
                start_month=january,
                months=12,
            )
            calibrate_historical_crushers(
                prepared, processing_cost_usd_per_tonne=35, calibrate_yields=True
            )
            years.append(year)
        except ValueError:
            continue
    return tuple(years)


def supported_historical_years() -> tuple[int, ...]:
    """Return evaluated years with complete local inputs and valid calibration."""
    usda, bank = _historical_paths()
    if not usda.is_file() or not bank.is_file():
        return ()
    return _validated_years(
        usda, bank, usda.stat().st_mtime_ns, bank.stat().st_mtime_ns
    )


def run_dashboard_simulation(config: RunConfig) -> DashboardResult:
    """Execute a supported configuration using existing calibrated runners."""
    if config.source not in ("Synthetic", "Historical USDA/World Bank"):
        raise ValueError(f"Unsupported data source: {config.source}")
    if config.mode not in (
        "Baseline",
        "Baseline vs Drought",
        "Argentina Drought",
        "Production Shock",
    ):
        raise ValueError(f"Unsupported simulation mode: {config.mode}")
    paired = config.mode == "Baseline vs Drought"
    shocked = config.mode != "Baseline"
    if (
        config.source == "Historical USDA/World Bank"
        and shocked
        and not paired
        and "production_shock" not in signature(run_historical_market).parameters
    ):
        raise RuntimeError(
            "The historical runner is outdated. Stop and restart the Streamlit server "
            "to load the updated code."
        )
    if config.source == "Synthetic" and config.year != 2025:
        raise ValueError("Synthetic simulation is available only for 2025")
    if config.source != "Synthetic" and config.year not in supported_historical_years():
        raise ValueError(
            f"Historical year {config.year} has incomplete or unsupported data"
        )
    pricing = PriceDynamicsConfig(
        price_sensitivity=config.price_sensitivity,
        maximum_monthly_change=config.maximum_monthly_change,
    )
    change = (
        config.shock_percent
        if config.shock_percent is not None
        else -float(config.reduction_percent)
    )
    shock = (
        ProductionShockConfig(
            region=config.shock_region,
            percentage_change=change,
            start_month=config.drought_start,
            end_month=config.drought_end,
        )
        if shocked
        else None
    )
    if shocked and (
        shock is None
        or shock.start_month is None
        or shock.end_month is None
        or shock.start_month.year != config.year
        or shock.end_month.year != config.year
    ):
        raise ValueError("Drought months must lie within the simulation year")
    if config.source == "Synthetic":
        case = replace(create_demo(), price_config=pricing)
        if paired:
            assert shock is not None
            comparison = run_production_shock_comparison(
                case.state,
                case.flows,
                shock_config=shock,
                months=12,
                agent_factory=lambda: create_agents(case.crusher_config),
                crusher_configs={"crusher-ar": case.crusher_config},
                price_dynamics_config=case.price_config,
            )
            return DashboardResult(config, comparison.baseline, comparison)
        baseline = run_simulation(
            case.state,
            create_agents(case.crusher_config),
            months=12,
            crusher_configs={"crusher-ar": case.crusher_config},
            monthly_flows=apply_production_shock(case.flows, config=shock)
            if shock is not None
            else case.flows,
            price_dynamics_config=case.price_config,
        )
    else:
        prepared = _load_year(config.year)
        prices = {
            price.commodity: price.price_usd_per_tonne
            for price in prepared.reference_prices
        }
        crushers = calibrate_historical_crushers(
            prepared, processing_cost_usd_per_tonne=35, calibrate_yields=True
        )
        traders = {
            region: TraderConfig(
                reference_soybean_price_usd_per_tonne=prices[Commodity.SOYBEANS],
                reference_soybean_meal_price_usd_per_tonne=prices[
                    Commodity.SOYBEAN_MEAL
                ],
                reference_soybean_oil_price_usd_per_tonne=prices[Commodity.SOYBEAN_OIL],
                maximum_monthly_purchase_tonnes=5000,
            )
            for region in Region
        }
        if paired:
            assert shock is not None
            comparison = compare_historical_market(
                prepared,
                crusher_configs=crushers,
                trader_configs=traders,
                price_dynamics_config=pricing,
                drought_config=shock,
            )
            return DashboardResult(config, comparison.baseline, comparison)
        if shocked:
            baseline = run_historical_market(
                prepared,
                crusher_configs=crushers,
                trader_configs=traders,
                price_dynamics_config=pricing,
                production_shock=shock,
            )
        else:
            baseline = run_historical_market(
                prepared,
                crusher_configs=crushers,
                trader_configs=traders,
                price_dynamics_config=pricing,
            )
    return DashboardResult(config, baseline, None)
