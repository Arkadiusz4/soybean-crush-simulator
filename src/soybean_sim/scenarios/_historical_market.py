"""Screen physical feasibility and execute unchanged historical inputs."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Never

from pydantic import ValidationError

from soybean_sim import market
from soybean_sim.agents import (
    Agent,
    AgentDecision,
    AgentIdentity,
    AgentType,
    Crusher,
    CrusherConfig,
    CrusherDecisionError,
    Producer,
    ProducerConfig,
    ProducerDecisionError,
    Trader,
    TraderConfig,
    TraderDecisionError,
)
from soybean_sim.market import (
    MarketFlowType,
    PriceDynamicsConfig,
    validate_external_trades,
)
from soybean_sim.models import Commodity, MarketSnapshot, Region
from soybean_sim.simulation import SimulationError, SimulationResult, run_simulation

from ._comparison import ScenarioComparison, run_argentina_drought_comparison
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
    prepare_historical_market,
)


def preflight_historical_market(
    prepared: HistoricalMarketInputs,
    *,
    crusher_configs: Mapping[Region, CrusherConfig],
    trader_configs: Mapping[Region, TraderConfig],
    price_dynamics_config: PriceDynamicsConfig,
    calibration: bool = False,
) -> HistoricalPreflight:
    """Screen data integrity and optimistic product budgets without executing a month.

    Args:
        prepared (HistoricalMarketInputs): Selected original data and modeled flows.
        crusher_configs (Mapping[Region, CrusherConfig]): Explicit regional capacities.
        trader_configs (Mapping[Region, TraderConfig]): Explicit regional trading rules.
        price_dynamics_config (PriceDynamicsConfig): Explicit regional price settings.
        calibration (bool): Validate structure without requiring all demand fulfilled.

    Returns:
        HistoricalPreflight: Warnings and blockers; clearance is not execution success.
    """
    metadata = None
    try:
        if type(calibration) is not bool:
            raise ValueError("calibration must be a bool")
        if isinstance(prepared, HistoricalMarketInputs):
            metadata = HistoricalMetadata.model_validate(
                prepared.metadata.model_dump(warnings=False)
            )
        data = _validated_preparation(prepared)
        crushers, traders, _ = _validated_settings(
            crusher_configs, trader_configs, price_dynamics_config
        )
        errors = () if calibration else _product_deficits(data, crushers, traders)
        assumptions = tuple(
            warning
            for warning in data.metadata.assumptions
            if not (
                calibration
                and data.metadata.include_historical_trade
                and warning.startswith("USDA annual imports/exports")
            )
        )
        return HistoricalPreflight(
            metadata=data.metadata,
            warnings=(
                *assumptions,
                (
                    "Calibration records unmet consumption and uses agent trade "
                    "matching; USDA gross trade is reference data only."
                    if calibration
                    else "Screening assumes full Crusher utilization with boundary "
                    "imports and exports. Actual margin decisions can still prevent "
                    "settlement; clearance does not guarantee physical execution."
                    if data.metadata.include_historical_trade
                    else "Screening assumes full utilization and optimistic soybean "
                    "purchases up to each Trader's limit. Purchases follow crushing; "
                    "only earlier purchases can supply processing. Passing this bound "
                    "does not guarantee margin-based processing or physical execution."
                ),
            ),
            blocking_errors=errors,
        )
    except HistoricalMarketError as exc:
        return exc.diagnostics
    except (ValueError, TypeError, OverflowError) as exc:
        return HistoricalPreflight(
            metadata=metadata,
            warnings=() if metadata is None else metadata.assumptions,
            blocking_errors=(f"Historical preflight blocked: {exc}",),
        )


def _validated_preparation(prepared: HistoricalMarketInputs) -> HistoricalMarketInputs:
    """Revalidate coverage, stock identity, dates, and original annual allocations.

    Args:
        prepared (HistoricalMarketInputs): Candidate historical preparation.

    Returns:
        HistoricalMarketInputs: Validated inputs with no silent repairs or scaling.
    """
    if not isinstance(prepared, HistoricalMarketInputs):
        raise ValueError("prepared must be HistoricalMarketInputs")
    data = HistoricalMarketInputs.model_validate(prepared.model_dump(warnings=False))
    metadata = data.metadata
    expected = prepare_historical_market(
        data.balances,
        data.reference_prices,
        marketing_year=metadata.marketing_year,
        price_month=metadata.reference_price_month,
        start_month=metadata.months[0],
        months=len(metadata.months),
        flow_config=metadata.allocation,
        usda_source=metadata.usda_source,
        world_bank_source=metadata.world_bank_source,
        include_historical_trade=metadata.include_historical_trade,
    )
    if (
        data.balances != expected.balances
        or data.reference_prices != expected.reference_prices
    ):
        raise ValueError(
            "prepared balances/prices must contain unique, complete "
            "selected observations"
        )
    if data.state != expected.state:
        raise ValueError(
            "initial inventories/prices/month differ from selected beginning "
            "stocks and reference observations"
        )
    indexed = {
        (flow.month, flow.region, flow.commodity, flow.flow_type): flow
        for flow in data.flows
    }
    if len(indexed) != len(data.flows):
        raise ValueError("duplicate monthly flow keys in historical inputs")
    actual = tuple(indexed[key] for key in sorted(indexed))
    if actual != expected.flows:
        raise ValueError(
            "monthly flow coverage/dates/quantities differ from modeled "
            "annual allocations"
        )
    if (
        validate_external_trades(data.external_trade_flows)
        != expected.external_trade_flows
    ):
        raise ValueError(
            "external trade quantities/coverage differ from annual allocation"
        )
    return data


def _validated_settings(
    crusher_configs: Mapping[Region, CrusherConfig],
    trader_configs: Mapping[Region, TraderConfig],
    price_config: PriceDynamicsConfig,
) -> tuple[
    dict[Region, CrusherConfig], dict[Region, TraderConfig], PriceDynamicsConfig
]:
    """Require explicit immutable configurations for every historical market region.

    Args:
        crusher_configs (Mapping[Region, CrusherConfig]): Regional processing settings.
        trader_configs (Mapping[Region, TraderConfig]): Regional trading settings.
        price_config (PriceDynamicsConfig): Regional dynamic-price settings.

    Returns:
        tuple: Complete, revalidated crusher, trader, and price configurations.
    """
    if not isinstance(crusher_configs, Mapping) or not isinstance(
        trader_configs, Mapping
    ):
        raise ValueError("Crusher and Trader configurations must be regional mappings")
    if set(crusher_configs) != set(Region) or set(trader_configs) != set(Region):
        raise ValueError(
            "explicit Crusher and Trader configurations must cover all four regions"
        )
    if not isinstance(price_config, PriceDynamicsConfig):
        raise ValueError("price_dynamics_config must be PriceDynamicsConfig")
    if any(
        not isinstance(config, CrusherConfig) for config in crusher_configs.values()
    ):
        raise ValueError("crusher_configs must contain CrusherConfig values")
    if any(not isinstance(config, TraderConfig) for config in trader_configs.values()):
        raise ValueError("trader_configs must contain TraderConfig values")
    return (
        {
            region: CrusherConfig.model_validate(config.model_dump(warnings=False))
            for region, config in crusher_configs.items()
        },
        {
            region: TraderConfig.model_validate(config.model_dump(warnings=False))
            for region, config in trader_configs.items()
        },
        PriceDynamicsConfig.model_validate(price_config.model_dump(warnings=False)),
    )


def _product_deficits(
    data: HistoricalMarketInputs,
    crushers: Mapping[Region, CrusherConfig],
    traders: Mapping[Region, TraderConfig],
) -> tuple[str, ...]:
    """Find product deficits under optimistic capacity and soybean-availability bounds.

    Built-in agents never request meal/oil purchases, so these products cannot be
    imported by this setup. Soybean purchases are bounded by Trader demand, not
    guaranteed executions. Margins and exports can further reduce actual output.

    Args:
        data (HistoricalMarketInputs): Unchanged inventories and monthly consumption.
        crushers (Mapping[Region, CrusherConfig]): Validated physical upper capacities.
        traders (Mapping[Region, TraderConfig]): Upper limits on soybean purchases.

    Returns:
        tuple[str, ...]: First deficit per regional product with quantitative context.
    """
    stocks = {
        (item.region, item.commodity): item.quantity_tonnes
        for item in data.state.snapshot.inventories
    }
    consumption = {
        (flow.month, flow.region, flow.commodity): flow.quantity_tonnes
        for flow in data.flows
        if flow.flow_type == MarketFlowType.CONSUMPTION
    }
    errors = []
    balances = {(item.region, item.commodity): item for item in data.balances}
    boundary = {
        (item.month, item.region, item.commodity, item.is_import): item.quantity_tonnes
        for item in data.external_trade_flows
    }
    for region in sorted(Region):
        config = crushers[region]
        crush_limits = _crushing_limits(data, region, config, traders[region])
        for commodity, fraction in (
            (Commodity.SOYBEAN_MEAL, config.meal_yield_fraction),
            (Commodity.SOYBEAN_OIL, config.oil_yield_fraction),
        ):
            opening = stocks[region, commodity]
            used, imported, exported = 0.0, 0.0, 0.0
            for month in data.metadata.months:
                try:
                    used = math.fsum((used, consumption[month, region, commodity]))
                    imported = math.fsum(
                        (imported, boundary.get((month, region, commodity, True), 0.0))
                    )
                    exported = math.fsum(
                        (exported, boundary.get((month, region, commodity, False), 0.0))
                    )
                    output = crush_limits[month] * fraction
                    available = math.fsum((opening, imported, output))
                    required = math.fsum((used, exported))
                except OverflowError as exc:
                    raise ValueError(
                        f"{region.value}/{commodity.value} {month}: "
                        "product budget overflow with configured monthly capacity "
                        f"{config.monthly_capacity_tonnes:g} t"
                    ) from exc
                if not math.isfinite(available) or not math.isfinite(used):
                    raise ValueError(
                        f"{region.value}/{commodity.value} {month}: "
                        "product budget overflow"
                    )
                if required > available:
                    errors.append(
                        f"{region.value}/{commodity.value} {month}: "
                        f"cumulative consumption {used:,.2f} t + exports "
                        f"{exported:,.2f} t exceeds opening stocks {opening:,.2f} t "
                        f"+ imports {imported:,.2f} t + maximum crushing output "
                        f"{output:,.2f} t "
                        f"(available upper bound {available:,.2f} t; "
                        f"shortfall {required - available:,.2f} t). "
                        f"Cumulative soybean input bound {crush_limits[month]:,.2f} t "
                        "accounts for processing capacity and soybean availability. "
                        "Meal/oil reference production adds no inventory. "
                        f"USDA annual imports_tonnes="
                        f"{balances[region, commodity].imports_tonnes!r} "
                        + (
                            "is allocated through the explicit clearing boundary."
                            if data.metadata.include_historical_trade
                            else "is reference data, not an executed import."
                        )
                    )
                    break
    return tuple(errors)


def _crushing_limits(
    data: HistoricalMarketInputs,
    region: Region,
    crusher: CrusherConfig,
    trader: TraderConfig,
) -> dict[date, float]:
    """Bound crush by capacity and soybean inflows after reserving boundary exports.

    Args:
        data (HistoricalMarketInputs): Original stocks and modeled soybean supply.
        region (Region): Regional processor and shared inventory location.
        crusher (CrusherConfig): Maximum input capacity, ignoring margin reductions.
        trader (TraderConfig): Optimistic prior purchases in reference-only mode;
            explicit boundary mode uses its own import schedule instead.

    Returns:
        dict[date, float]: Optimistic cumulative input tonnes by simulation month.
    """
    soybeans = next(
        item.quantity_tonnes
        for item in data.state.snapshot.inventories
        if (item.region, item.commodity) == (region, Commodity.SOYBEANS)
    )
    supply = {
        item.month: item.quantity_tonnes
        for item in data.flows
        if item.region == region and item.commodity == Commodity.SOYBEANS
    }
    purchase_limit = trader.maximum_monthly_purchase_tonnes * max(
        trader.low_purchase_fraction,
        trader.normal_purchase_fraction,
        trader.high_purchase_fraction,
    )
    cumulative_capacity = 0.0
    cumulative_exports = 0.0
    boundary = {
        (item.month, item.is_import): item.quantity_tonnes
        for item in data.external_trade_flows
        if item.region == region and item.commodity == Commodity.SOYBEANS
    }
    limits = {}
    for index, month in enumerate(data.metadata.months):
        try:
            cumulative_capacity = math.fsum(
                (cumulative_capacity, crusher.monthly_capacity_tonnes)
            )
            imports = (
                boundary[month, True]
                if data.metadata.include_historical_trade
                else purchase_limit
                if index
                else 0
            )
            soybeans = math.fsum((soybeans, supply[month], imports))
            cumulative_exports = math.fsum(
                (cumulative_exports, boundary.get((month, False), 0.0))
            )
        except OverflowError as exc:
            raise ValueError(
                f"{region.value}/soybeans {month}: crushing budget overflow; "
                f"monthly capacity {crusher.monthly_capacity_tonnes:g} t"
            ) from exc
        available_for_crush = soybeans - cumulative_exports
        if available_for_crush < 0:
            raise ValueError(
                f"{region.value}/soybeans {month}: cumulative exports "
                f"{cumulative_exports:g} t exceed supply plus imports {soybeans:g} t"
            )
        limits[month] = min(cumulative_capacity, available_for_crush)
    return limits


def _create_agents(
    prepared: HistoricalMarketInputs,
    *,
    crusher_configs: Mapping[Region, CrusherConfig],
    trader_configs: Mapping[Region, TraderConfig],
) -> tuple[Agent, ...]:
    """Construct real agents with selected price baselines and explicit rules.

    Args:
        prepared (HistoricalMarketInputs): Prepared global reference observations.
        crusher_configs (Mapping[Region, CrusherConfig]): Validated regional processors.
        trader_configs (Mapping[Region, TraderConfig]): Validated regional traders.

    Returns:
        tuple[Agent, ...]: One Producer, Crusher, and Trader per supported region.
    """
    prices = {
        price.commodity: price.price_usd_per_tonne
        for price in prepared.reference_prices
    }
    agents: list[Agent] = []
    for region in sorted(Region):
        agents.extend(
            (
                Producer(
                    AgentIdentity(
                        agent_id=f"producer-{region.value}",
                        agent_type=AgentType.PRODUCER,
                        region=region,
                    ),
                    ProducerConfig(
                        reference_price_usd_per_tonne=prices[Commodity.SOYBEANS]
                    ),
                ),
                Crusher(
                    AgentIdentity(
                        agent_id=f"crusher-{region.value}",
                        agent_type=AgentType.CRUSHER,
                        region=region,
                    ),
                    crusher_configs[region],
                ),
                Trader(
                    AgentIdentity(
                        agent_id=f"trader-{region.value}",
                        agent_type=AgentType.TRADER,
                        region=region,
                    ),
                    trader_configs[region],
                ),
            )
        )
    return tuple(agents)


@dataclass
class _Progress:
    """Track the current month and scenario for execution-failure diagnostics."""

    month: date | None = None
    scenario: str = "baseline"


@dataclass(frozen=True)
class _ObservedCrusher(Crusher):
    """Track the first decision while retaining concrete Crusher validation."""

    progress: _Progress
    scenario: str

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Record current execution context before forwarding unchanged intentions.

        Args:
            snapshot (MarketSnapshot): Actual engine observation for this month.

        Returns:
            tuple[AgentDecision, ...]: Original Crusher decisions unchanged.
        """
        self.progress.month, self.progress.scenario = snapshot.month, self.scenario
        return super().decide(snapshot)


def _observe_month(
    agents: tuple[Agent, ...], progress: _Progress, scenario: str
) -> tuple[Agent, ...]:
    """Observe the first registered Crusher, preserving its configuration checks.

    Args:
        agents (tuple[Agent, ...]): Fresh real agents.
        progress (_Progress): Local diagnostic record, never an economic input.
        scenario (str): Baseline/drought label to include on failure.

    Returns:
        tuple[Agent, ...]: Identical strategies with one diagnostic Crusher subtype.
    """
    return tuple(
        _ObservedCrusher(agent.identity, agent.config, progress, scenario)
        if isinstance(agent, Crusher) and agent.identity.region == Region.ARGENTINA
        else agent
        for agent in agents
    )


def run_historical_market(
    prepared: HistoricalMarketInputs,
    *,
    crusher_configs: Mapping[Region, CrusherConfig],
    trader_configs: Mapping[Region, TraderConfig],
    price_dynamics_config: PriceDynamicsConfig,
    drought_config: ArgentinaDroughtConfig | None = None,
    production_shock: ProductionShockConfig | None = None,
    crusher_parameter_shock: CrusherParameterShockConfig | None = None,
) -> SimulationResult:
    """Preflight the physical engine and return only complete histories.

    Args:
        prepared (HistoricalMarketInputs): Original data and modeled monthly inputs.
        crusher_configs (Mapping[Region, CrusherConfig]): Explicit regional capacities.
        trader_configs (Mapping[Region, TraderConfig]): Explicit regional trading rules.
        price_dynamics_config (PriceDynamicsConfig): Regional pricing assumptions.
        drought_config (ArgentinaDroughtConfig | None): Optional soybean supply shock.
        production_shock (ProductionShockConfig | None): Regional supply change.
        crusher_parameter_shock: Optional capacity and processing-cost experiment.

    Returns:
        SimulationResult: Complete run, or diagnostics retaining execution context.
    """
    report = preflight_historical_market(
        prepared,
        crusher_configs=crusher_configs,
        trader_configs=trader_configs,
        price_dynamics_config=price_dynamics_config,
        calibration=True,
    )
    if not report.can_run:
        raise HistoricalMarketError(report)
    if drought_config is not None and production_shock is not None:
        raise ValueError("Only one production shock may be supplied")
    crushers, traders, price_config = _validated_settings(
        crusher_configs, trader_configs, price_dynamics_config
    )
    if crusher_parameter_shock is not None:
        if drought_config is not None or production_shock is not None:
            raise ValueError("Only one experiment is supported")
        crushers = apply_crusher_parameter_shock(
            crushers, config=crusher_parameter_shock
        )
    progress = _Progress()
    agents = _observe_month(
        _create_agents(prepared, crusher_configs=crushers, trader_configs=traders),
        progress,
        "crusher configuration"
        if crusher_parameter_shock is not None
        else "production shock"
        if production_shock is not None
        else "drought"
        if drought_config is not None
        else "baseline",
    )
    try:
        return run_simulation(
            prepared.state,
            agents,
            monthly_flows=apply_consumption_shock(
                prepared.flows, config=production_shock
            )
            if isinstance(production_shock, ConsumptionShockConfig)
            else apply_production_shock(prepared.flows, config=production_shock)
            if production_shock is not None
            else (
                prepared.flows
                if drought_config is None
                else apply_argentina_drought(prepared.flows, config=drought_config)
            ),
            external_trade_flows=None,
            demand_aware_consumption=True,
            months=len(prepared.metadata.months),
            price_dynamics_config=price_config,
            crusher_configs={
                f"crusher-{region.value}": config for region, config in crushers.items()
            },
        )
    except (
        CrusherDecisionError,
        ProducerDecisionError,
        TraderDecisionError,
        market.FlowSettlementError,
        market.ExternalTradeError,
        market.MarketPressureError,
        market.SettlementError,
        market.TradeMatchingError,
        market.TradeSettlementError,
        market.PriceDynamicsError,
        market.RegionalPriceError,
        SimulationError,
        ValidationError,
    ) as exc:
        _execution_failure(report, progress, exc)


def compare_historical_market(
    prepared: HistoricalMarketInputs,
    *,
    crusher_configs: Mapping[Region, CrusherConfig],
    trader_configs: Mapping[Region, TraderConfig],
    price_dynamics_config: PriceDynamicsConfig,
    drought_config: ArgentinaDroughtConfig
    | ConsumptionShockConfig
    | ProductionShockConfig
    | CrusherParameterShockConfig,
) -> ScenarioComparison:
    """Attempt paired runs from identical historical starting conditions.

    Args:
        prepared (HistoricalMarketInputs): Baseline inputs, never scaled or repaired.
        crusher_configs (Mapping[Region, CrusherConfig]): Common processing assumptions.
        trader_configs (Mapping[Region, TraderConfig]): Common trading assumptions.
        price_dynamics_config (PriceDynamicsConfig): Common price dynamics settings.
        drought_config (ArgentinaDroughtConfig): New Argentina soybean supply loss.

    Returns:
        ScenarioComparison: Complete comparable runs, or explicit blocking diagnostics.
    """
    report = preflight_historical_market(
        prepared,
        crusher_configs=crusher_configs,
        trader_configs=trader_configs,
        price_dynamics_config=price_dynamics_config,
        calibration=True,
    )
    if not report.can_run:
        raise HistoricalMarketError(report)
    crushers, traders, price_config = _validated_settings(
        crusher_configs, trader_configs, price_dynamics_config
    )
    progress = _Progress()
    calls = 0

    def fresh_agents() -> tuple[Agent, ...]:
        """Build independent agents and attach only a scenario/month diagnostic label.

        Returns:
            tuple[Agent, ...]: Fresh real strategies for the corresponding paired run.
        """
        nonlocal calls
        label = "baseline" if calls == 0 else "drought"
        calls += 1
        return _observe_month(
            _create_agents(prepared, crusher_configs=crushers, trader_configs=traders),
            progress,
            label,
        )

    try:
        return run_argentina_drought_comparison(
            prepared.state,
            prepared.flows,
            drought_config=drought_config,
            months=len(prepared.metadata.months),
            agent_factory=fresh_agents,
            crusher_configs={
                f"crusher-{region.value}": config for region, config in crushers.items()
            },
            price_dynamics_config=price_config,
            external_trade_flows=None,
            demand_aware_consumption=True,
        )
    except (
        CrusherDecisionError,
        ProducerDecisionError,
        TraderDecisionError,
        market.FlowSettlementError,
        market.ExternalTradeError,
        market.MarketPressureError,
        market.SettlementError,
        market.TradeMatchingError,
        market.TradeSettlementError,
        market.PriceDynamicsError,
        market.RegionalPriceError,
        SimulationError,
        ScenarioError,
        ValidationError,
    ) as exc:
        _execution_failure(report, progress, exc)


def _execution_failure(
    report: HistoricalPreflight, progress: _Progress, error: Exception
) -> Never:
    """Retain the last observed context without returning partial histories.

    Args:
        report (HistoricalPreflight): Successful structural/capacity screen.
        progress (_Progress): Last actual engine observation.
        error (Exception): Existing market or simulation domain failure.

    Returns:
        Never: Always raises HistoricalMarketError with the blocking execution reason.
    """
    diagnostics = report.model_copy(
        update={
            "blocking_errors": (
                "Historical physical execution blocked; last observed decisions: "
                f"{progress.scenario} {progress.month}. {error}",
            )
        }
    )
    raise HistoricalMarketError(diagnostics) from error
