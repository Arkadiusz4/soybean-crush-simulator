"""Match interregional trade intentions greedily against shared starting stocks."""

from collections.abc import Iterable
from datetime import date

from pydantic import ValidationError

from soybean_sim.agents import AgentDecision, AgentIdentity, DecisionAction
from soybean_sim.models import Commodity, Inventory, Region

from .contracts import TradeExecution

_InventoryKey = tuple[Region, Commodity]
_IntentionKey = tuple[str, DecisionAction, Commodity, date]


class TradeMatchingError(ValueError):
    """Matching inputs are invalid, duplicated, or contain conflicting identities."""


def match_interregional_trades(
    decisions: Iterable[AgentDecision],
    inventories: Iterable[Inventory],
    *,
    month: date,
) -> tuple[TradeExecution, ...]:
    """Match interregional trades without mutating inputs.

    Prioritize commodity, then seller/buyer region and ID. Sellers share starting
    stock; purchases do not replenish it. Skip CRUSH and zero-quantity matches.
    """
    if type(month) is not date or month.day != 1:
        raise TradeMatchingError("month must be a date on the first day of a month")
    intentions = _trade_intentions(decisions, month)
    available = _inventory_availability(inventories)
    ordered = sorted(
        intentions,
        key=lambda decision: (
            decision.commodity,
            decision.agent.region,
            decision.agent.agent_id,
        ),
    )

    sellers = [item for item in ordered if item.action == DecisionAction.OFFER_FOR_SALE]
    buyers = [item for item in ordered if item.action == DecisionAction.PURCHASE]

    return _match_intentions(sellers, buyers, available)


def _validate_decision(decision: AgentDecision, month: date) -> AgentDecision:
    """Revalidate nested decision data and require the requested matching month."""
    if not isinstance(decision, AgentDecision):
        raise TradeMatchingError("decisions must contain AgentDecision records")
    try:
        validated = AgentDecision.model_validate(decision.model_dump(warnings=False))
    except ValidationError as exc:
        raise TradeMatchingError(f"Invalid decision: {exc}") from exc

    if validated.month != month:
        raise TradeMatchingError(
            f"Agent {validated.agent.agent_id!r}: decision month {validated.month} "
            f"does not match requested month {month}"
        )

    return validated


def _trade_intentions(
    decisions: Iterable[AgentDecision], month: date
) -> tuple[AgentDecision, ...]:
    """Require stable identities and unique trade keys, excluding validated crushing."""
    identities: dict[str, AgentIdentity] = {}
    seen: set[_IntentionKey] = set()
    intentions: list[AgentDecision] = []
    for decision in decisions:
        validated = _validate_decision(decision, month)
        agent = validated.agent

        previous = identities.setdefault(agent.agent_id, agent)
        if previous != agent:
            raise TradeMatchingError(
                f"Conflicting identities for agent ID {agent.agent_id!r}"
            )

        if validated.action == DecisionAction.CRUSH:
            continue

        key = (agent.agent_id, validated.action, validated.commodity, month)
        if key in seen:
            raise TradeMatchingError(
                f"Duplicate trade intention for agent {agent.agent_id!r}: "
                f"{validated.action.value}/{validated.commodity.value} in {month}"
            )

        seen.add(key)
        intentions.append(validated)

    return tuple(intentions)


def _inventory_availability(
    inventories: Iterable[Inventory],
) -> dict[_InventoryKey, float]:
    """Validate unique regional stocks and copy quantities into local counters."""
    available: dict[_InventoryKey, float] = {}
    for inventory in inventories:
        if not isinstance(inventory, Inventory):
            raise TradeMatchingError("inventories must contain Inventory records")
        try:
            validated = Inventory.model_validate(inventory.model_dump(warnings=False))
        except ValidationError as exc:
            raise TradeMatchingError(f"Invalid inventory: {exc}") from exc

        key = (validated.region, validated.commodity)
        if key in available:
            raise TradeMatchingError(
                f"Duplicate inventory for {validated.region.value}/"
                f"{validated.commodity.value}"
            )

        available[key] = validated.quantity_tonnes

    return available


def _match_intentions(
    sellers: list[AgentDecision],
    buyers: list[AgentDecision],
    available: dict[_InventoryKey, float],
) -> tuple[TradeExecution, ...]:
    """Allocate ordered offers to eligible buyers using batch-local counters."""
    remaining_demand = [buyer.quantity_tonnes for buyer in buyers]
    trades: list[TradeExecution] = []
    for seller in sellers:
        remaining_offer = seller.quantity_tonnes
        inventory_key = (seller.agent.region, seller.commodity)

        for buyer_index, buyer in enumerate(buyers):
            if (
                seller.commodity != buyer.commodity
                or seller.agent.region == buyer.agent.region
            ):
                continue

            quantity = min(
                remaining_offer,
                remaining_demand[buyer_index],
                available.get(inventory_key, 0),
            )

            if quantity == 0:
                continue

            trades.append(
                TradeExecution(
                    month=seller.month,
                    seller_agent_id=seller.agent.agent_id,
                    buyer_agent_id=buyer.agent.agent_id,
                    source_region=seller.agent.region,
                    destination_region=buyer.agent.region,
                    commodity=seller.commodity,
                    quantity_tonnes=quantity,
                )
            )

            remaining_offer = max(0.0, remaining_offer - quantity)
            remaining_demand[buyer_index] = max(
                0.0, remaining_demand[buyer_index] - quantity
            )
            available[inventory_key] = max(0.0, available[inventory_key] - quantity)

    return tuple(trades)
