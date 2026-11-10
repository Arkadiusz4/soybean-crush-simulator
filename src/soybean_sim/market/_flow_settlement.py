"""Apply soybean supply and meal/oil consumption; product supply is reference-only.

Crushing handles soybean use and must precede consumption needing its outputs.
"""

import math
from collections.abc import Iterable
from datetime import date

from pydantic import ValidationError

from soybean_sim.models import Commodity, Inventory, MarketSnapshot, Region

from ._contracts import MarketFlow, MarketFlowType
from ._models import MarketState

_InventoryKey = tuple[Region, Commodity]
_FlowKey = tuple[date, Region, Commodity, MarketFlowType]


class FlowSettlementError(ValueError):
    """Invalid monthly flows or insufficient regional inventory."""


def settle_monthly_flows(
    state: MarketState, flows: Iterable[MarketFlow]
) -> MarketState:
    """Return updated inventories, preserving prices, month, and input models.

    Args:
        state (MarketState): Current market state before flow settlement.
        flows (Iterable[MarketFlow]): Monthly external flows to apply.

    Returns:
        MarketState: Market state with updated inventories.
    """
    initial = _validate_state(state)
    starting = {
        (stock.region, stock.commodity): stock.quantity_tonnes
        for stock in initial.snapshot.inventories
    }
    validated_flows = _validated_flows(flows, initial.snapshot.month, starting)

    changes = _physical_changes(validated_flows)
    if not changes:
        return initial

    inventories = tuple(
        Inventory(
            region=region,
            commodity=commodity,
            quantity_tonnes=_ending_quantity(
                quantity,
                changes.get((region, commodity), 0),
                f"{region.value}/{commodity.value}",
            ),
        )
        for (region, commodity), quantity in sorted(starting.items())
    )

    return MarketState(
        snapshot=MarketSnapshot(
            month=initial.snapshot.month,
            prices=initial.snapshot.prices,
            inventories=inventories,
        )
    )


def _validate_state(state: MarketState) -> MarketState:
    """Revalidate the snapshot and inventory uniqueness.

    Args:
        state (MarketState): Candidate market state to revalidate.

    Returns:
        MarketState: Revalidated market state.
    """
    if not isinstance(state, MarketState):
        raise FlowSettlementError("state must be a MarketState")
    try:
        return MarketState.model_validate(state.model_dump(warnings=False))
    except ValidationError as exc:
        raise FlowSettlementError(f"Invalid market state: {exc}") from exc


def _validated_flows(
    flows: Iterable[MarketFlow], month: date, starting: dict[_InventoryKey, float]
) -> tuple[MarketFlow, ...]:
    """Validate flow data, timing, unique keys, and stock coverage.

    Args:
        flows (Iterable[MarketFlow]): Candidate monthly flows.
        month (date): Expected first-of-month date for all flows.
        starting (dict[tuple[Region, Commodity], float]): Starting inventory
            quantities by region and commodity.

    Returns:
        tuple[MarketFlow, ...]: Validated unique flows for the month.
    """
    seen: set[_FlowKey] = set()
    validated_flows: list[MarketFlow] = []
    for flow in flows:
        if not isinstance(flow, MarketFlow):
            raise FlowSettlementError("flows must contain MarketFlow records")
        try:
            validated = MarketFlow.model_validate(flow.model_dump(warnings=False))
        except ValidationError as exc:
            raise FlowSettlementError(f"Invalid flow: {exc}") from exc

        if validated.month != month:
            raise FlowSettlementError(
                f"Flow month {validated.month} does not match market month {month}"
            )

        key = (month, validated.region, validated.commodity, validated.flow_type)
        if key in seen:
            raise FlowSettlementError(
                f"Duplicate flow for {validated.region.value}/"
                f"{validated.commodity.value}/{validated.flow_type.value} in {month}"
            )

        seen.add(key)

        if (validated.region, validated.commodity) not in starting:
            raise FlowSettlementError(
                f"Missing inventory for {validated.region.value}/"
                f"{validated.commodity.value}"
            )
        validated_flows.append(validated)

    return tuple(validated_flows)


def _physical_changes(flows: tuple[MarketFlow, ...]) -> dict[_InventoryKey, float]:
    """Select soybean supply and meal/oil consumption.

    Args:
        flows (tuple[MarketFlow, ...]): Validated monthly flows.

    Returns:
        dict[tuple[Region, Commodity], float]: Signed inventory changes by region and
            commodity.
    """
    changes: dict[_InventoryKey, float] = {}
    for flow in flows:
        if flow.flow_type == MarketFlowType.SUPPLY:
            if flow.commodity == Commodity.SOYBEANS:
                changes[flow.region, flow.commodity] = flow.quantity_tonnes
            continue

        if flow.commodity == Commodity.SOYBEANS:
            raise FlowSettlementError(
                "Unsupported soybean consumption flow; crushing handles soybean use"
            )
        changes[flow.region, flow.commodity] = -flow.quantity_tonnes

    return changes


def _ending_quantity(starting: float, change: float, context: str) -> float:
    """Calculate finite, nonnegative ending stock.

    Args:
        starting (float): Starting inventory quantity in tonnes.
        change (float): Signed inventory change in tonnes.
        context (str): Label used in insufficient-stock error messages.

    Returns:
        float: Ending inventory quantity in tonnes.
    """
    if change < 0 and -change > starting:
        raise FlowSettlementError(
            f"Insufficient inventory for {context}: "
            f"consumption {-change:g} tonnes exceeds available {starting:g} tonnes"
        )

    try:
        ending = math.fsum((starting, change))
    except OverflowError as exc:
        raise FlowSettlementError(f"Inventory overflow for {context}") from exc
    if not math.isfinite(ending) or ending < 0:
        raise FlowSettlementError(f"Invalid resulting inventory for {context}")

    return ending
