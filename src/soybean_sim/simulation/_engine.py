"""Run deterministic monthly agent decisions followed by one physical settlement."""

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Final

from soybean_sim.agents import (
    Agent,
    AgentDecision,
    AgentIdentity,
    AgentType,
    Crusher,
    CrusherConfig,
)
from soybean_sim.market import MarketState, settle_decisions
from soybean_sim.models import MarketSnapshot, Region

from . import _calendar
from ._models import MonthlyResult, SimulationResult

_MONTHS_PER_YEAR: Final = 12
_RegisteredAgent = tuple[AgentIdentity, Agent]


class SimulationError(ValueError):
    """Run parameters or an agent's registration or returned decisions are invalid."""


def run_simulation(
    initial_state: MarketState,
    agents: Iterable[Agent],
    *,
    crusher_configs: Mapping[str, CrusherConfig],
    months: int,
) -> SimulationResult:
    """Run monthly decisions and settlement, preserving constant global prices.

    Agents observe one shared starting snapshot in ascending ID order. Empty
    registrations are valid. The final state belongs to the last completed month;
    agent and settlement errors propagate without returning a partial history.

    Args:
        initial_state (MarketState): Market observations and inventories at the start of
            the first month.
        agents (Iterable[Agent]): Agents with unique IDs and stable identities in
            represented regions.
        crusher_configs (Mapping[str, CrusherConfig]): Validated processing settings
            keyed by every registered crusher's ID.
        months (int): Positive number of monthly steps within the supported calendar
            range.

    Returns:
        SimulationResult: Initial state, complete monthly history, and the last
            completed month's ending state.
    """
    initial = MarketState.from_snapshot(initial_state.snapshot)
    _validate_duration(initial.snapshot.month, months)
    registered_agents, processing_configs = _prepare_agents(
        initial, agents, crusher_configs
    )
    state = initial
    history: list[MonthlyResult] = []
    for month_index in range(months):
        if month_index:
            state = _calendar.advance_market_month(state)

        decisions = _collect_decisions(registered_agents, state.snapshot)
        ending_state, settlement_results = settle_decisions(
            state, decisions, crusher_configs=processing_configs
        )

        history.append(
            MonthlyResult(
                month=state.snapshot.month,
                starting_state=state,
                decisions=decisions,
                settlement_results=settlement_results,
                ending_state=ending_state,
            )
        )
        state = ending_state

    return SimulationResult(
        initial_state=initial, monthly_results=tuple(history), final_state=state
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
    registered_agents: tuple[_RegisteredAgent, ...], snapshot: MarketSnapshot
) -> tuple[AgentDecision, ...]:
    """Collect ordered intentions and verify their registered attribution.

    Args:
        registered_agents (tuple[_RegisteredAgent, ...]): Validated identity/agent pairs
            in execution order.
        snapshot (MarketSnapshot): Shared starting observation for the current month.

    Returns:
        tuple[AgentDecision, ...]: Agent-attributed intentions preserving registration
            and per-agent decision order.
    """
    decisions: list[AgentDecision] = []
    for identity, agent in registered_agents:
        for decision in agent.decide(snapshot):
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
