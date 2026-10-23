"""Settle crushing sequentially against regional stocks while deferring all trades."""

import math
from collections.abc import Iterable, Mapping

from pydantic import ValidationError

from soybean_sim.agents import (
    AgentDecision,
    AgentIdentity,
    AgentType,
    CrusherConfig,
    DecisionAction,
)
from soybean_sim.models import Commodity, Inventory, MarketSnapshot, Region

from .models import MarketState, SettlementOutcome, SettlementResult

_InventoryKey = tuple[Region, Commodity]


class SettlementError(ValueError):
    """A settlement batch contains invalid data or cannot produce valid stocks."""


def settle_decisions(
    state: MarketState,
    decisions: Iterable[AgentDecision],
    *,
    crusher_configs: Mapping[str, CrusherConfig],
) -> tuple[MarketState, tuple[SettlementResult, ...]]:
    """Return a new state and results in agent ID, action, commodity, quantity order.

    Allocate crushing sequentially in that order, trusting requested capacity.
    Each crusher ID may submit one CRUSH request. Trade intentions remain deferred;
    all work is local, so a failure leaves the supplied state unchanged.
    """
    quantities = {
        (stock.region, stock.commodity): stock.quantity_tonnes
        for stock in state.snapshot.inventories
    }

    ordered_decisions, processing_configs = _prepare_decisions(
        state.snapshot, decisions, crusher_configs, quantities
    )

    results: list[SettlementResult] = []
    for decision in ordered_decisions:
        if decision.action == DecisionAction.CRUSH:
            result = _settle_crush(
                decision, processing_configs[decision.agent.agent_id], quantities
            )
        else:
            result = _defer_trade(decision)
        results.append(result)

    snapshot = MarketSnapshot(
        month=state.snapshot.month,
        prices=state.snapshot.prices,
        inventories=tuple(
            Inventory(region=region, commodity=commodity, quantity_tonnes=quantity)
            for (region, commodity), quantity in sorted(quantities.items())
        ),
    )

    return MarketState(snapshot=snapshot), tuple(results)


def _prepare_decisions(
    snapshot: MarketSnapshot,
    decisions: Iterable[AgentDecision],
    crusher_configs: Mapping[str, CrusherConfig],
    quantities: Mapping[_InventoryKey, float],
) -> tuple[tuple[AgentDecision, ...], dict[str, CrusherConfig]]:
    """Validate the entire batch and referenced configs before physical processing."""
    submitted = tuple(decisions)
    identities: dict[str, AgentIdentity] = {}
    processing_configs: dict[str, CrusherConfig] = {}
    for decision in submitted:
        _validate_decision(decision, snapshot, quantities, identities)
        if decision.action != DecisionAction.CRUSH:
            continue

        agent_id = decision.agent.agent_id
        if agent_id in processing_configs:
            raise SettlementError(f"Duplicate CRUSH request for agent {agent_id!r}")

        processing_configs[agent_id] = _processing_config(decision, crusher_configs)
        _require_crush_inventories(decision, quantities)

    ordered = tuple(
        sorted(
            submitted,
            key=lambda decision: (
                decision.agent.agent_id,
                decision.action,
                decision.commodity,
                decision.quantity_tonnes,
            ),
        )
    )

    return ordered, processing_configs


def _validate_decision(
    decision: AgentDecision,
    snapshot: MarketSnapshot,
    quantities: Mapping[_InventoryKey, float],
    identities: dict[str, AgentIdentity],
) -> None:
    """Check decision validity, month, identity consistency, and market references."""
    try:
        AgentDecision.model_validate(decision.model_dump())
    except ValidationError as exc:
        raise SettlementError(f"Invalid decision: {exc}") from exc

    agent = decision.agent
    if decision.month != snapshot.month:
        raise SettlementError(
            f"Agent {agent.agent_id!r}: decision month {decision.month} "
            f"does not match market month {snapshot.month}"
        )

    previous = identities.setdefault(agent.agent_id, agent)
    if previous != agent:
        raise SettlementError(f"Conflicting identities for agent ID {agent.agent_id!r}")
    _validate_market_references(decision, snapshot, quantities)

    if (
        decision.action == DecisionAction.CRUSH
        and agent.agent_type != AgentType.CRUSHER
    ):
        raise SettlementError(
            f"CRUSH decision agent {agent.agent_id!r} must be a crusher"
        )


def _validate_market_references(
    decision: AgentDecision,
    snapshot: MarketSnapshot,
    quantities: Mapping[_InventoryKey, float],
) -> None:
    """Require known regions and commodities without assuming trade stock ownership."""
    known_regions = {region for region, _ in quantities}
    if decision.agent.region not in known_regions:
        raise SettlementError(
            f"Agent {decision.agent.agent_id!r}: region {decision.agent.region.value} "
            "is not represented in market state"
        )

    known_commodities = {price.commodity for price in snapshot.prices} | {
        commodity for _, commodity in quantities
    }
    if decision.commodity not in known_commodities:
        raise SettlementError(
            f"Agent {decision.agent.agent_id!r}: commodity {decision.commodity.value} "
            "is not represented in market state"
        )


def _processing_config(
    decision: AgentDecision, crusher_configs: Mapping[str, CrusherConfig]
) -> CrusherConfig:
    """Resolve and revalidate the crusher config using its existing yield validators."""
    agent_id = decision.agent.agent_id
    config = crusher_configs.get(agent_id)
    if config is None:
        raise SettlementError(
            f"Missing processing configuration for crusher {agent_id!r}"
        )

    if not isinstance(config, CrusherConfig):
        raise SettlementError(f"Crusher {agent_id!r}: expected a CrusherConfig")
    try:
        return CrusherConfig.model_validate(config.model_dump())
    except ValidationError as exc:
        raise SettlementError(
            f"Crusher {agent_id!r}: invalid processing configuration: {exc}"
        ) from exc


def _require_crush_inventories(
    decision: AgentDecision, quantities: Mapping[_InventoryKey, float]
) -> None:
    """Require explicit soybean, meal, and oil records even for zero input requests."""
    for commodity in Commodity:
        if (decision.agent.region, commodity) not in quantities:
            raise SettlementError(
                f"Crusher {decision.agent.agent_id!r}: missing inventory for "
                f"{decision.agent.region.value}/{commodity.value}"
            )


def _settle_crush(
    decision: AgentDecision,
    config: CrusherConfig,
    quantities: dict[_InventoryKey, float],
) -> SettlementResult:
    """Consume bounded input and add configured outputs to a batch-local stock map."""
    region = decision.agent.region
    soybean_key = (region, Commodity.SOYBEANS)
    meal_key = (region, Commodity.SOYBEAN_MEAL)
    oil_key = (region, Commodity.SOYBEAN_OIL)
    executed = min(decision.quantity_tonnes, quantities[soybean_key])
    updates = {
        soybean_key: quantities[soybean_key] - executed,
        meal_key: quantities[meal_key] + executed * config.meal_yield_fraction,
        oil_key: quantities[oil_key] + executed * config.oil_yield_fraction,
    }

    if any(
        not math.isfinite(quantity) or quantity < 0 for quantity in updates.values()
    ):
        raise SettlementError(
            f"Crusher {decision.agent.agent_id!r}: "
            "resulting inventories must be finite and nonnegative"
        )

    quantities.update(updates)

    return SettlementResult(
        decision=decision,
        requested_tonnes=decision.quantity_tonnes,
        executed_tonnes=executed,
        unfulfilled_tonnes=decision.quantity_tonnes - executed,
        outcome=SettlementOutcome.CRUSH_SETTLED,
    )


def _defer_trade(decision: AgentDecision) -> SettlementResult:
    """Report a purchase or sale as wholly unfulfilled without changing inventory."""
    return SettlementResult(
        decision=decision,
        requested_tonnes=decision.quantity_tonnes,
        executed_tonnes=0,
        unfulfilled_tonnes=decision.quantity_tonnes,
        outcome=SettlementOutcome.TRADE_MATCHING_NOT_IMPLEMENTED,
    )
