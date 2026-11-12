"""Run deterministic legacy or physical market cycles with constant price values."""

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Final

from pydantic import ValidationError

from soybean_sim.agents import (
    Agent,
    AgentDecision,
    AgentIdentity,
    AgentType,
    Crusher,
    CrusherConfig,
    DecisionAction,
)
from soybean_sim.market import (
    MarketFlow,
    MarketFlowType,
    MarketState,
    RegionalPrice,
    match_interregional_trades,
    settle_decisions,
    settle_matched_trades,
    settle_monthly_flows,
    snapshot_for_region,
    validate_regional_prices,
)
from soybean_sim.models import Commodity, MarketSnapshot, Region

from . import _calendar
from ._models import MonthlyResult, SimulationResult

_MONTHS_PER_YEAR: Final = 12
_RegisteredAgent = tuple[AgentIdentity, Agent]
_FlowKey = tuple[date, Region, Commodity, MarketFlowType]


class SimulationError(ValueError):
    """Run parameters or an agent's registration or returned decisions are invalid."""


def run_simulation(
    initial_state: MarketState,
    agents: Iterable[Agent],
    *,
    crusher_configs: Mapping[str, CrusherConfig],
    months: int,
    regional_prices: Iterable[RegionalPrice] | None = None,
    monthly_flows: Iterable[MarketFlow] | None = None,
) -> SimulationResult:
    """Run monthly decisions and settlement, preserving constant global prices.

    Agents decide in ascending ID order. Without regional prices they share the
    global snapshot; otherwise each receives its region's price view with the same
    inventories. Regional values stay constant while their dates advance.

    Args:
        initial_state: Observations and inventories at the start of the first month.
        agents: Agents with unique IDs and stable identities in represented regions.
        crusher_configs: Processing settings keyed by each registered crusher's ID.
        months: Positive step count within the supported calendar range.
        regional_prices: Optional initial-month regional series covering every agent
            region's commodities. Invalid regional data propagates its domain error.
        monthly_flows: None keeps legacy crushing and deferred trades. Any iterable,
            including an empty one, enables the physical production/trade/consumption
            cycle. Generate flows separately; all records must fit this run's period.

    Returns:
        Initial state, complete monthly history, and the last month's ending state.
        Recorded market states retain global prices; agent views are temporary.
    """
    initial = MarketState.from_snapshot(initial_state.snapshot)
    _validate_duration(initial.snapshot.month, months)
    registered_agents, processing_configs = _prepare_agents(
        initial, agents, crusher_configs
    )
    current_regional_prices = _prepare_regional_prices(
        initial.snapshot, registered_agents, regional_prices
    )
    flow_schedule = _prepare_monthly_flows(initial.snapshot, monthly_flows, months)
    state = initial
    history: list[MonthlyResult] = []
    for month_index in range(months):
        if month_index:
            state = _calendar.advance_market_month(state)
            if current_regional_prices is not None:
                current_regional_prices = _calendar.advance_regional_prices(
                    current_regional_prices
                )

        if flow_schedule is None:
            decisions = _collect_decisions(
                registered_agents, state.snapshot, current_regional_prices
            )
            ending_state, settlement_results = settle_decisions(
                state, decisions, crusher_configs=processing_configs
            )
            monthly = MonthlyResult(
                month=state.snapshot.month,
                starting_state=state,
                decisions=decisions,
                settlement_results=settlement_results,
                ending_state=ending_state,
            )
        else:
            monthly = _physical_month(
                state,
                registered_agents,
                processing_configs,
                flow_schedule.get(state.snapshot.month, ()),
                current_regional_prices,
            )
        history.append(monthly)
        state = monthly.ending_state

    return SimulationResult(
        initial_state=initial, monthly_results=tuple(history), final_state=state
    )


def _prepare_monthly_flows(
    snapshot: MarketSnapshot, monthly_flows: Iterable[MarketFlow] | None, months: int
) -> dict[date, tuple[MarketFlow, ...]] | None:
    """Validate the complete flow schedule without testing future stock availability.

    Args:
        snapshot: Initial month and represented inventories.
        monthly_flows: Optional caller-supplied flow collection.
        months: Validated simulation duration.

    Returns:
        Sorted flows grouped by month, or None for legacy mode.
    """
    if monthly_flows is None:
        return None

    try:
        records = iter(monthly_flows)
    except TypeError as exc:
        raise SimulationError(
            "monthly_flows must be an iterable of MarketFlow records"
        ) from exc

    inventories = {(stock.region, stock.commodity) for stock in snapshot.inventories}
    grouped: dict[date, list[MarketFlow]] = {}
    seen: set[_FlowKey] = set()
    for record in records:
        flow = _validate_scheduled_flow(record, snapshot.month, months, inventories)
        key = (flow.month, flow.region, flow.commodity, flow.flow_type)

        if key in seen:
            raise SimulationError(
                f"Duplicate monthly flow for {flow.region.value}/"
                f"{flow.commodity.value}/{flow.flow_type.value} in {flow.month}"
            )

        seen.add(key)
        grouped.setdefault(flow.month, []).append(flow)

    return {
        month: tuple(
            sorted(
                flows, key=lambda flow: (flow.region, flow.commodity, flow.flow_type)
            )
        )
        for month, flows in sorted(grouped.items())
    }


def _validate_scheduled_flow(
    flow: MarketFlow,
    first_month: date,
    months: int,
    inventories: set[tuple[Region, Commodity]],
) -> MarketFlow:
    """Revalidate one flow's period, stock coverage, and supported consumption type.

    Args:
        flow: Candidate monthly record.
        first_month: First simulation month.
        months: Validated simulation duration.
        inventories: Existing regional commodity inventory keys.

    Returns:
        A revalidated flow for the requested simulation period.
    """
    if not isinstance(flow, MarketFlow):
        raise SimulationError("monthly_flows must contain MarketFlow records")

    try:
        validated = MarketFlow.model_validate(flow.model_dump(warnings=False))
    except ValidationError as exc:
        raise SimulationError(f"Invalid monthly flow: {exc}") from exc

    offset = (
        (validated.month.year - first_month.year) * _MONTHS_PER_YEAR
        + validated.month.month
        - first_month.month
    )

    if not 0 <= offset < months:
        raise SimulationError(
            f"Flow month {validated.month} is outside the simulation period"
        )

    if (validated.region, validated.commodity) not in inventories:
        raise SimulationError(
            f"Flow inventory {validated.region.value}/{validated.commodity.value} "
            "is not represented in the initial state"
        )

    if (
        validated.flow_type == MarketFlowType.CONSUMPTION
        and validated.commodity == Commodity.SOYBEANS
    ):
        raise SimulationError("Unsupported soybean consumption flow")

    return validated


def _physical_month(
    opening: MarketState,
    registered_agents: tuple[_RegisteredAgent, ...],
    crusher_configs: Mapping[str, CrusherConfig],
    flows: tuple[MarketFlow, ...],
    regional_prices: tuple[RegionalPrice, ...] | None,
) -> MonthlyResult:
    """Apply production, decisions, crushing, trades, and consumption in that order.

    Args:
        opening: State before this month's physical activity.
        registered_agents: Ordered, validated registrations.
        crusher_configs: Processing settings keyed by crusher ID.
        flows: Validated current-month flows, including reference production.
        regional_prices: Current regional values or None for global observations.

    Returns:
        Complete physical activity history with the opening and final states.
    """
    produced = settle_monthly_flows(
        opening, (flow for flow in flows if flow.flow_type == MarketFlowType.SUPPLY)
    )

    decisions = _collect_decisions(
        registered_agents, produced.snapshot, regional_prices
    )

    crushed, settlements = settle_decisions(
        produced,
        (decision for decision in decisions if decision.action == DecisionAction.CRUSH),
        crusher_configs=crusher_configs,
    )
    trades = match_interregional_trades(
        decisions, crushed.snapshot.inventories, month=opening.snapshot.month
    )

    traded = settle_matched_trades(crushed, trades)
    ending = settle_monthly_flows(
        traded, (flow for flow in flows if flow.flow_type == MarketFlowType.CONSUMPTION)
    )

    return MonthlyResult(
        month=opening.snapshot.month,
        starting_state=opening,
        ending_state=ending,
        decisions=decisions,
        settlement_results=settlements,
        physical_cycle=True,
        market_flows=flows,
        executed_trades=trades,
    )


def _validate_duration(first_month: date, months: int) -> None:
    """Require a positive integer duration that fits Python's supported calendar.

    Args:
        first_month (date): First calendar day of the simulation's initial month.
        months (int): Requested positive integer number of monthly steps.

    Returns:
        None: No value is returned.
    """
    if type(months) is not int or months <= 0:
        raise SimulationError("months must be a positive integer")

    available_months = (
        (date.max.year - first_month.year) * _MONTHS_PER_YEAR
        + _MONTHS_PER_YEAR
        - first_month.month
        + 1
    )

    if months > available_months:
        raise SimulationError(
            "requested duration exceeds the maximum supported calendar year"
        )


def _prepare_regional_prices(
    snapshot: MarketSnapshot,
    registered_agents: tuple[_RegisteredAgent, ...],
    regional_prices: Iterable[RegionalPrice] | None,
) -> tuple[RegionalPrice, ...] | None:
    """Validate the complete regional series and agent coverage before execution.

    Args:
        snapshot: Initial global observations.
        registered_agents: Validated identity/agent pairs.
        regional_prices: Optional caller-supplied regional observations.

    Returns:
        Sorted regional prices, or None for the original global-price behavior.
    """
    if regional_prices is None:
        return None

    validated = validate_regional_prices(snapshot, regional_prices)
    for region in sorted({identity.region for identity, _ in registered_agents}):
        snapshot_for_region(snapshot, validated, region)

    return validated


def _prepare_agents(
    initial_state: MarketState,
    agents: Iterable[Agent],
    crusher_configs: Mapping[str, CrusherConfig],
) -> tuple[tuple[_RegisteredAgent, ...], dict[str, CrusherConfig]]:
    """Validate registrations and consistent decision and settlement configurations.

    Args:
        initial_state (MarketState): Validated initial state defining represented
            regions.
        agents (Iterable[Agent]): Agents to register with unique IDs.
        crusher_configs (Mapping[str, CrusherConfig]): Processing settings keyed by
            crusher agent ID.

    Returns:
        tuple[tuple[_RegisteredAgent, ...], dict[str, CrusherConfig]]: Registrations
            sorted by agent ID and revalidated configurations for registered crushers.
    """
    regions = {stock.region for stock in initial_state.snapshot.inventories}
    registered: dict[str, _RegisteredAgent] = {}
    processing_configs: dict[str, CrusherConfig] = {}
    for agent in agents:
        identity = _validate_identity(agent, regions)
        if identity.agent_id in registered:
            raise SimulationError(f"Duplicate agent ID {identity.agent_id!r}")

        if identity.agent_type == AgentType.CRUSHER:
            config = _crusher_config(identity.agent_id, crusher_configs)

            if isinstance(agent, Crusher) and agent.config != config:
                raise SimulationError(
                    f"Crusher {identity.agent_id!r}: agent configuration does not "
                    "match the settlement configuration in crusher_configs"
                )
            processing_configs[identity.agent_id] = config

        registered[identity.agent_id] = (identity, agent)

    return tuple(
        registered[agent_id] for agent_id in sorted(registered)
    ), processing_configs


def _validate_identity(agent: Agent, regions: set[Region]) -> AgentIdentity:
    """Require a validated identity belonging to a region represented by inventories.

    Args:
        agent (Agent): Agent exposing an AgentIdentity to register.
        regions (set[Region]): Regions represented by initial inventory records.

    Returns:
        AgentIdentity: Revalidated agent identity in a represented region.
    """
    if not isinstance(agent.identity, AgentIdentity):
        raise SimulationError("agents must expose an AgentIdentity")

    identity = AgentIdentity.model_validate(agent.identity.model_dump())
    if identity.region not in regions:
        raise SimulationError(
            f"Agent {identity.agent_id!r}: region {identity.region.value} "
            "is not represented in market state"
        )

    return identity


def _crusher_config(
    agent_id: str, crusher_configs: Mapping[str, CrusherConfig]
) -> CrusherConfig:
    """Validate processing parameters even for crushers that later abstain.

    Args:
        agent_id (str): Crusher ID whose processing settings are required.
        crusher_configs (Mapping[str, CrusherConfig]): Processing settings indexed by
            crusher agent ID.

    Returns:
        CrusherConfig: Revalidated configuration for the requested crusher.
    """
    config = crusher_configs.get(agent_id)
    if config is None:
        raise SimulationError(
            f"Missing processing configuration for crusher {agent_id!r}"
        )

    if not isinstance(config, CrusherConfig):
        raise SimulationError(f"Crusher {agent_id!r}: expected a CrusherConfig")

    return CrusherConfig.model_validate(config.model_dump())


def _collect_decisions(
    registered_agents: tuple[_RegisteredAgent, ...],
    snapshot: MarketSnapshot,
    regional_prices: tuple[RegionalPrice, ...] | None = None,
) -> tuple[AgentDecision, ...]:
    """Collect ordered intentions and verify their registered attribution.

    Args:
        registered_agents: Validated identity/agent pairs in execution order.
        snapshot: Global starting observations for the current month.
        regional_prices: Current-month regional series, or None for global views.

    Returns:
        Agent intentions preserving registration and per-agent decision order.
    """
    decisions: list[AgentDecision] = []
    for identity, agent in registered_agents:
        observation = (
            snapshot
            if regional_prices is None
            else snapshot_for_region(snapshot, regional_prices, identity.region)
        )
        for decision in agent.decide(observation):
            if not isinstance(decision, AgentDecision):
                raise SimulationError(
                    f"Agent {identity.agent_id!r} returned an invalid decision type"
                )

            if decision.agent != identity:
                raise SimulationError(
                    f"Agent {identity.agent_id!r} returned a decision "
                    "for another identity"
                )

            decisions.append(decision)

    return tuple(decisions)
