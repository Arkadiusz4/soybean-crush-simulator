"""Apply matched trades atomically using starting regional inventory limits."""

import math
from collections.abc import Iterable
from datetime import date

from pydantic import ValidationError

from soybean_sim.models import Commodity, Inventory, MarketSnapshot, Region

from .contracts import TradeExecution
from .models import MarketState

_InventoryKey = tuple[Region, Commodity]
_Transfers = dict[_InventoryKey, list[float]]


class TradeSettlementError(ValueError):
    """Invalid trade settlement inputs or insufficient inventory."""


def settle_matched_trades(
    state: MarketState, trades: Iterable[TradeExecution]
) -> MarketState:
    """Return a new state with matched transfers applied and prices/month unchanged.

    Validate the complete batch against starting stocks; incoming trades cannot
    fund outgoing sales. Identical records are rejected. Empty trades are a no-op.
    Nonempty batches return inventories sorted by region and commodity.
    """
    initial = _validate_state(state)
    starting = {
        (stock.region, stock.commodity): stock.quantity_tonnes
        for stock in initial.snapshot.inventories
    }

    outgoing, incoming = _aggregate_transfers(trades, initial.snapshot.month, starting)
    if not outgoing:
        return initial

    inventories = _updated_inventories(starting, outgoing, incoming)

    return MarketState(
        snapshot=MarketSnapshot(
            month=initial.snapshot.month,
            prices=initial.snapshot.prices,
            inventories=inventories,
        )
    )


def _validate_state(state: MarketState) -> MarketState:
    """Revalidate nested prices and unique inventory records before settlement."""
    if not isinstance(state, MarketState):
        raise TradeSettlementError("state must be a MarketState")
    try:
        return MarketState.model_validate(state.model_dump(warnings=False))
    except ValidationError as exc:
        raise TradeSettlementError(f"Invalid market state: {exc}") from exc


def _validate_trade(trade: TradeExecution, month: date) -> TradeExecution:
    """Require a valid matched execution belonging to the state's snapshot month."""
    if not isinstance(trade, TradeExecution):
        raise TradeSettlementError("trades must contain TradeExecution records")
    try:
        validated = TradeExecution.model_validate(trade.model_dump(warnings=False))
    except ValidationError as exc:
        raise TradeSettlementError(f"Invalid trade: {exc}") from exc

    if validated.month != month:
        raise TradeSettlementError(
            f"Trade month {validated.month} does not match market month {month}"
        )

    return validated


def _aggregate_transfers(
    trades: Iterable[TradeExecution],
    month: date,
    starting: dict[_InventoryKey, float],
) -> tuple[_Transfers, _Transfers]:
    """Validate unique trades and existing endpoints, collecting both transfer sides."""
    seen: set[TradeExecution] = set()
    outgoing: _Transfers = {}
    incoming: _Transfers = {}

    for trade in trades:
        validated = _validate_trade(trade, month)

        if validated in seen:
            raise TradeSettlementError(
                f"Duplicate trade for {validated.seller_agent_id!r} to "
                f"{validated.buyer_agent_id!r}, {validated.commodity.value} in {month}"
            )

        seen.add(validated)

        for role, region in (
            ("source", validated.source_region),
            ("destination", validated.destination_region),
        ):
            if (region, validated.commodity) not in starting:
                raise TradeSettlementError(
                    f"Missing {role} inventory for "
                    f"{region.value}/{validated.commodity.value}"
                )

        source = (validated.source_region, validated.commodity)
        destination = (validated.destination_region, validated.commodity)
        outgoing.setdefault(source, []).append(validated.quantity_tonnes)
        incoming.setdefault(destination, []).append(validated.quantity_tonnes)

    return outgoing, incoming


def _sum_quantities(quantities: Iterable[float], context: str) -> float:
    """Sum stocks or transfers accurately, rejecting overflow and negative results."""
    try:
        total = math.fsum(quantities)
    except OverflowError as exc:
        raise TradeSettlementError(f"Quantity overflow for {context}") from exc

    if not math.isfinite(total) or total < 0:
        raise TradeSettlementError(f"Invalid resulting quantity for {context}")

    return total


def _transfer_totals(transfers: _Transfers) -> dict[_InventoryKey, float]:
    """Aggregate transfers in a fixed order for reproducible floating-point sums."""
    return {
        (region, commodity): _sum_quantities(
            sorted(quantities), f"{region.value}/{commodity.value} transfers"
        )
        for (region, commodity), quantities in sorted(transfers.items())
    }


def _updated_inventories(
    starting: dict[_InventoryKey, float], outgoing: _Transfers, incoming: _Transfers
) -> tuple[Inventory, ...]:
    """Check total sales against starting stocks, then construct sorted inventories."""
    exported = _transfer_totals(outgoing)
    for (region, commodity), quantity in exported.items():
        available = starting[region, commodity]
        if quantity > available:
            raise TradeSettlementError(
                "Insufficient starting inventory for "
                f"{region.value}/{commodity.value}: "
                f"outgoing {quantity:g} tonnes exceeds available {available:g} tonnes"
            )

    imported = _transfer_totals(incoming)

    return tuple(
        Inventory(
            region=region,
            commodity=commodity,
            quantity_tonnes=_sum_quantities(
                (
                    quantity,
                    -exported.get((region, commodity), 0),
                    imported.get((region, commodity), 0),
                ),
                f"{region.value}/{commodity.value} ending inventory",
            ),
        )
        for (region, commodity), quantity in sorted(starting.items())
    )
