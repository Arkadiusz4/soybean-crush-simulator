"""Execute paired runs and expose validated, immutable comparison histories."""

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from soybean_sim.agents import (
    Agent,
    AgentIdentity,
    Crusher,
    CrusherConfig,
    Producer,
    Trader,
)
from soybean_sim.market import (
    ExternalTradeFlow,
    MarketFlow,
    MarketFlowType,
    MarketState,
    PriceDynamicsConfig,
    RegionalPrice,
)
from soybean_sim.models import Commodity, Region
from soybean_sim.simulation import SimulationResult, run_simulation

from . import _metrics
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


class ScenarioComparison(BaseModel):
    """Retain paired histories once and derive same-month metrics on demand.

    The legacy ``drought`` and ``drought_config`` fields also carry generic
    flow-shock and Crusher-experiment results and their configuration. Crusher
    experiments additionally retain both regional parameter maps for validation.

    Args:
        baseline (SimulationResult): Physical baseline history with regional records.
        drought (SimulationResult): Comparable drought history.
        drought_config (ArgentinaDroughtConfig): Reduction that produced drought flows.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    baseline: SimulationResult
    drought: SimulationResult
    drought_config: (
        ArgentinaDroughtConfig
        | ConsumptionShockConfig
        | ProductionShockConfig
        | CrusherParameterShockConfig
    )

    baseline_crusher_configs: dict[Region, CrusherConfig] | None = None
    modified_crusher_configs: dict[Region, CrusherConfig] | None = None

    @field_validator("baseline", "drought", mode="after")
    @classmethod
    def revalidate_history(cls, value: SimulationResult) -> SimulationResult:
        """Revalidate nested fields even for model copies bypassing validation.

        Args:
            value (SimulationResult): Parsed result or existing model instance.

        Returns:
            SimulationResult: Revalidated result, including every nested record.
        """
        return SimulationResult.model_validate(value.model_dump(warnings=False))

    @field_validator("drought_config", mode="after")
    @classmethod
    def revalidate_drought_config(
        cls,
        value: ArgentinaDroughtConfig
        | ConsumptionShockConfig
        | ProductionShockConfig
        | CrusherParameterShockConfig,
    ) -> (
        ArgentinaDroughtConfig
        | ConsumptionShockConfig
        | ProductionShockConfig
        | CrusherParameterShockConfig
    ):
        """Validate configuration copies and serialized configuration fields.

        Args:
            value (ArgentinaDroughtConfig): Parsed settings or existing model instance.

        Returns:
            ArgentinaDroughtConfig: Revalidated reduction and date bounds.
        """
        return type(value).model_validate(value.model_dump(warnings=False))

    @model_validator(mode="after")
    def histories_are_comparable(self) -> Self:
        """Require common conditions, full records, and the configured supply shock.

        Returns:
            Self: Validated paired histories.
        """
        _metrics.validate_histories(self.baseline, self.drought)
        if isinstance(self.drought_config, CrusherParameterShockConfig):
            if (
                self.baseline_crusher_configs is None
                or self.modified_crusher_configs is None
            ):
                raise ValueError("Crusher comparison requires both configuration maps")
            if (
                apply_crusher_parameter_shock(
                    self.baseline_crusher_configs, config=self.drought_config
                )
                != self.modified_crusher_configs
            ):
                raise ValueError(
                    "Crusher configurations do not match the selected experiment"
                )
        elif (
            self.baseline_crusher_configs is not None
            or self.modified_crusher_configs is not None
        ):
            raise ValueError("Crusher metadata is only valid for Crusher experiments")
        flows = (
            flow
            for monthly in self.baseline.monthly_results
            for flow in monthly.market_flows
        )
        expected = (
            tuple(
                sorted(
                    flows, key=lambda f: (f.month, f.region, f.commodity, f.flow_type)
                )
            )
            if isinstance(self.drought_config, CrusherParameterShockConfig)
            else apply_consumption_shock(flows, config=self.drought_config)
            if isinstance(self.drought_config, ConsumptionShockConfig)
            else apply_production_shock(flows, config=self.drought_config)
            if isinstance(self.drought_config, ProductionShockConfig)
            else apply_argentina_drought(flows, config=self.drought_config)
        )
        actual = tuple(
            sorted(
                (
                    flow
                    for monthly in self.drought.monthly_results
                    for flow in monthly.market_flows
                ),
                key=lambda flow: (
                    flow.month,
                    flow.region,
                    flow.commodity,
                    flow.flow_type,
                ),
            )
        )
        if len(expected) != len(actual) or any(
            (left.month, left.region, left.commodity, left.flow_type)
            != (right.month, right.region, right.commodity, right.flow_type)
            or (
                left.quantity_tonnes != right.quantity_tonnes
                and not (
                    isinstance(self.drought_config, ProductionShockConfig)
                    and left.region == self.drought_config.region
                    and (
                        (
                            isinstance(self.drought_config, ConsumptionShockConfig)
                            and left.commodity == self.drought_config.commodity
                            and left.flow_type == MarketFlowType.CONSUMPTION
                        )
                        or (
                            not isinstance(self.drought_config, ConsumptionShockConfig)
                            and left.commodity == Commodity.SOYBEANS
                            and left.flow_type == MarketFlowType.SUPPLY
                        )
                    )
                    and (
                        self.drought_config.start_month is None
                        or left.month >= self.drought_config.start_month
                    )
                    and (
                        self.drought_config.end_month is None
                        or left.month <= self.drought_config.end_month
                    )
                    and math.isclose(
                        left.quantity_tonnes,
                        right.quantity_tonnes,
                        rel_tol=1e-12,
                        abs_tol=1e-9,
                    )
                )
            )
            for left, right in zip(expected, actual, strict=False)
        ):
            message = (
                "modified scenario flows do not match the configured production shock"
                if isinstance(self.drought_config, ProductionShockConfig)
                else "drought history flows do not match the configured "
                "Argentina supply reduction"
            )
            raise ValueError(message)
        if tuple(
            month.external_trade_flows for month in self.baseline.monthly_results
        ) != (
            tuple(month.external_trade_flows for month in self.drought.monthly_results)
        ):
            raise ValueError("baseline and drought external trade schedules must match")
        return self

    def monthly_metrics(self) -> tuple[_metrics.MonthlyComparison, ...]:
        """Derive signed comparisons without storing another copy of either history.

        Returns:
            tuple[MonthlyComparison, ...]: Metrics in shared chronological order.
        """
        try:
            validated = ScenarioComparison.model_validate(
                self.model_dump(warnings=False)
            )
            return tuple(
                _metrics.monthly_metrics(baseline, drought)
                for baseline, drought in zip(
                    validated.baseline.monthly_results,
                    validated.drought.monthly_results,
                    strict=True,
                )
            )
        except ValidationError as exc:
            raise ScenarioError(f"Invalid comparison metric: {exc}") from exc


def compare_simulation_results(
    baseline: SimulationResult,
    drought: SimulationResult,
    *,
    drought_config: ArgentinaDroughtConfig
    | ConsumptionShockConfig
    | ProductionShockConfig
    | CrusherParameterShockConfig,
) -> ScenarioComparison:
    """Validate recorded paired runs before comparing observations or executions.

    Args:
        baseline (SimulationResult): Baseline physical history.
        drought (SimulationResult): Drought physical history for the same months.
        drought_config (ArgentinaDroughtConfig): Applied production reduction.

    Returns:
        ScenarioComparison: Immutable histories with on-demand monthly metrics.
    """
    try:
        return ScenarioComparison(
            baseline=baseline, drought=drought, drought_config=drought_config
        )
    except ValidationError as exc:
        raise ScenarioError(f"Incompatible scenario comparison: {exc}") from exc


def run_argentina_drought_comparison(
    initial_state: MarketState,
    baseline_flows: Iterable[MarketFlow],
    *,
    drought_config: ArgentinaDroughtConfig
    | ConsumptionShockConfig
    | ProductionShockConfig
    | CrusherParameterShockConfig,
    months: int,
    agent_factory: Callable[[], Iterable[Agent]],
    crusher_configs: Mapping[str, CrusherConfig],
    price_dynamics_config: PriceDynamicsConfig,
    regional_prices: Iterable[RegionalPrice] | None = None,
    external_trade_flows: Iterable[ExternalTradeFlow] | None = None,
    demand_aware_consumption: bool = False,
) -> ScenarioComparison:
    """Run baseline and drought with common inputs and independently created agents.

    Custom factories must return fresh, equally configured agents for matching IDs.
    Concrete agent configurations are checked; private state of arbitrary Agent
    implementations remains the factory's responsibility.

    Args:
        initial_state (MarketState): Common initial stocks and global prices.
        baseline_flows (Iterable[MarketFlow]): Monthly inputs, materialized once.
        drought_config (ArgentinaDroughtConfig): Argentina supply reduction and bounds.
        months (int): Positive common simulation duration.
        agent_factory (Callable): Builds fresh, equally configured agents on each call.
        crusher_configs (Mapping[str, CrusherConfig]): Common settlement settings.
        price_dynamics_config (PriceDynamicsConfig): Required dynamic price settings.
        regional_prices (Iterable[RegionalPrice] | None): Common regional baselines,
            or None to initialize all inventory regions from global references.
        external_trade_flows (Iterable[ExternalTradeFlow] | None): Common boundary
            transfers, unchanged by drought and replacing endogenous trade matching.
        demand_aware_consumption (bool): Apply the same demand settlement to both runs.

    Returns:
        ScenarioComparison: Validated histories with only drought supply inputs changed.
    """
    try:
        flows = tuple(baseline_flows)
        prices = None if regional_prices is None else tuple(regional_prices)
        external = None if external_trade_flows is None else tuple(external_trade_flows)
    except TypeError as exc:
        raise ScenarioError(
            "scenario flows and regional prices must be iterable"
        ) from exc
    adjusted = (
        flows
        if isinstance(drought_config, CrusherParameterShockConfig)
        else apply_consumption_shock(flows, config=drought_config)
        if isinstance(drought_config, ConsumptionShockConfig)
        else apply_production_shock(flows, config=drought_config)
        if isinstance(drought_config, ProductionShockConfig)
        else apply_argentina_drought(flows, config=drought_config)
    )
    if not isinstance(price_dynamics_config, PriceDynamicsConfig):
        raise ScenarioError("price_dynamics_config must be a PriceDynamicsConfig")
    if not callable(agent_factory):
        raise ScenarioError("agent_factory must be callable")
    baseline_agents, drought_agents = _fresh_agents(agent_factory)
    configs = dict(crusher_configs)
    modified_configs = dict(configs)
    original_regions = modified_regions = None
    if isinstance(drought_config, CrusherParameterShockConfig):
        original_regions = {
            agent.identity.region: agent.config
            for agent in baseline_agents
            if isinstance(agent, Crusher)
        }
        if len(original_regions) != sum(
            isinstance(agent, Crusher) for agent in baseline_agents
        ):
            raise ScenarioError(
                "Crusher experiments require one concrete Crusher per region"
            )
        modified_regions = apply_crusher_parameter_shock(
            original_regions, config=drought_config
        )
        drought_agents = tuple(
            replace(agent, config=modified_regions[agent.identity.region])
            if isinstance(agent, Crusher)
            else agent
            for agent in drought_agents
        )
        modified_configs.update(
            {
                agent.identity.agent_id: agent.config
                for agent in drought_agents
                if isinstance(agent, Crusher)
            }
        )
    baseline = run_simulation(
        initial_state,
        baseline_agents,
        months=months,
        crusher_configs=configs,
        monthly_flows=flows,
        regional_prices=prices,
        price_dynamics_config=price_dynamics_config,
        external_trade_flows=external,
        demand_aware_consumption=demand_aware_consumption,
    )
    drought = run_simulation(
        initial_state,
        drought_agents,
        months=months,
        crusher_configs=modified_configs,
        monthly_flows=adjusted,
        regional_prices=prices,
        price_dynamics_config=price_dynamics_config,
        external_trade_flows=external,
        demand_aware_consumption=demand_aware_consumption,
    )
    if isinstance(drought_config, CrusherParameterShockConfig):
        return ScenarioComparison(
            baseline=baseline,
            drought=drought,
            drought_config=drought_config,
            baseline_crusher_configs=original_regions,
            modified_crusher_configs=modified_regions,
        )
    return compare_simulation_results(baseline, drought, drought_config=drought_config)


def run_production_shock_comparison(
    initial_state: MarketState,
    baseline_flows: Iterable[MarketFlow],
    *,
    shock_config: ProductionShockConfig
    | ConsumptionShockConfig
    | CrusherParameterShockConfig,
    months: int,
    agent_factory: Callable[[], Iterable[Agent]],
    crusher_configs: Mapping[str, CrusherConfig],
    price_dynamics_config: PriceDynamicsConfig,
    regional_prices: Iterable[RegionalPrice] | None = None,
    external_trade_flows: Iterable[ExternalTradeFlow] | None = None,
    demand_aware_consumption: bool = False,
) -> ScenarioComparison:
    """Run paired simulations with the configured regional soybean supply shock."""
    if not isinstance(
        shock_config, (ProductionShockConfig, CrusherParameterShockConfig)
    ):
        raise ScenarioError("shock_config must be a ProductionShockConfig")
    return run_argentina_drought_comparison(
        initial_state,
        baseline_flows,
        drought_config=shock_config,
        months=months,
        agent_factory=agent_factory,
        crusher_configs=crusher_configs,
        price_dynamics_config=price_dynamics_config,
        regional_prices=regional_prices,
        external_trade_flows=external_trade_flows,
        demand_aware_consumption=demand_aware_consumption,
    )


def _fresh_agents(
    factory: Callable[[], Iterable[Agent]],
) -> tuple[tuple[Agent, ...], tuple[Agent, ...]]:
    """Check independent registrations and equal concrete strategy configurations.

    Args:
        factory (Callable): Creates agents separately for each scenario.

    Returns:
        tuple: Materialized baseline and drought agents with matching identities.
    """
    first_batch, second_batch = factory(), factory()
    try:
        baseline, drought = tuple(first_batch), tuple(second_batch)
    except TypeError as exc:
        raise ScenarioError("agent_factory must return an iterable of agents") from exc
    if {id(agent) for agent in baseline} & {id(agent) for agent in drought}:
        raise ScenarioError(
            "agent_factory must create fresh agent instances for each run"
        )
    for agent in (*baseline, *drought):
        if not isinstance(
            getattr(agent, "identity", None), AgentIdentity
        ) or not callable(getattr(agent, "decide", None)):
            raise ScenarioError(
                "agent_factory must return agents with an AgentIdentity "
                "and decide method"
            )
    first = {agent.identity.agent_id: agent for agent in baseline}
    second = {agent.identity.agent_id: agent for agent in drought}
    if first.keys() != second.keys() or any(
        first[key].identity != second[key].identity for key in first
    ):
        raise ScenarioError("agent_factory must produce identical agent identities")
    for agent_id, agent in first.items():
        other = second[agent_id]
        if type(other) is not type(agent):
            raise ScenarioError(
                f"Agent {agent_id!r}: strategy implementations differ between scenarios"
            )
        if (
            isinstance(agent, (Producer, Crusher, Trader))
            and isinstance(other, (Producer, Crusher, Trader))
            and agent.config != other.config
        ):
            raise ScenarioError(
                f"Agent {agent_id!r}: strategy configurations differ between scenarios"
            )
    return baseline, drought
