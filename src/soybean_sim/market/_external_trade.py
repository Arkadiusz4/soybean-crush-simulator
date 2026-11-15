"""Record and settle explicit movements across an open-market accounting boundary."""

import math
from collections.abc import Iterable
from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from soybean_sim.models import (
    Commodity,
    Inventory,
    MarketSnapshot,
    NonNegativeTonnes,
    Region,
)

from ._models import MarketState


class ExternalMarket(StrEnum):
    """Identify the unmodeled clearing boundary, without adding a fifth region."""

    CLEARING_BOUNDARY = "external_market"


class ExternalTradeFlow(BaseModel):
    """Record scheduled physical trade with exactly one external endpoint.

    The boundary clears gross trade without asserting bilateral USDA origins.
    It has no invented opening stock: its signed counterpart is the recorded
    transfer, outside the four-region inventory system.

    Args:
        month (date): First day of the modeled transfer month.
        source (Region | ExternalMarket): Inventory supplier or external boundary.
        destination (Region | ExternalMarket): Inventory recipient or boundary.
        commodity (Commodity): Commodity transferred without conversion.
        quantity_tonnes (NonNegativeTonnes): Finite nonnegative physical tonnes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    month: Annotated[date, Field(strict=True)]
    source: Region | ExternalMarket
    destination: Region | ExternalMarket
    commodity: Commodity
    quantity_tonnes: NonNegativeTonnes

    @field_validator("month")
    @classmethod
    def month_starts_on_first_day(cls, month: date) -> date:
        """Require an explicit monthly boundary date.

        Args:
            month (date): Candidate calendar month.

        Returns:
            date: Unchanged first-of-month date.
        """
        if month.day != 1:
            raise ValueError("month must be the first day of a month")
        return month

    @model_validator(mode="after")
    def one_endpoint_is_external(self) -> Self:
        """Separate boundary transfers from internal matched trades.

        Returns:
            Self: Transfer with exactly one regional endpoint.
        """
        if isinstance(self.source, Region) == isinstance(self.destination, Region):
            raise ValueError("exactly one endpoint must be the external market")
        return self

    @property
    def region(self) -> Region:
        """Identify the regional inventory affected by this transfer.

        Returns:
            Region: The sole modeled endpoint.
        """
        return (
            self.source if isinstance(self.source, Region) else Region(self.destination)
        )

    @property
    def is_import(self) -> bool:
        """Identify an inflow from the clearing boundary.

        Returns:
            bool: True for regional imports, False for exports.
        """
        return isinstance(self.source, ExternalMarket)


class ExternalTradeError(ValueError):
    """A boundary schedule or physical inventory transfer cannot be settled."""


def validate_external_trades(
    flows: Iterable[ExternalTradeFlow],
) -> tuple[ExternalTradeFlow, ...]:
    """Validate a unique, deterministic boundary schedule without changing stocks.

    Args:
        flows (Iterable[ExternalTradeFlow]): Scheduled transfers, including known zeros.

    Returns:
        tuple[ExternalTradeFlow, ...]: Revalidated flows sorted by period and endpoints.
    """
    seen = set()
    validated = []
    try:
        for flow in flows:
            if not isinstance(flow, ExternalTradeFlow):
                raise ExternalTradeError("Expected ExternalTradeFlow records")
            record = ExternalTradeFlow.model_validate(flow.model_dump(warnings=False))
            key = (record.month, record.region, record.commodity, record.is_import)
            if key in seen:
                raise ExternalTradeError(f"Duplicate external trade: {key}")
            seen.add(key)
            validated.append(record)
    except (TypeError, ValidationError) as exc:
        raise ExternalTradeError(f"Invalid external trade schedule: {exc}") from exc
    return tuple(
        sorted(
            validated,
            key=lambda item: (
                item.month,
                item.region,
                item.commodity,
                item.is_import,
            ),
        )
    )


def settle_external_trades(
    state: MarketState,
    flows: Iterable[ExternalTradeFlow],
) -> MarketState:
    """Apply full boundary transfers atomically, rejecting unavailable export stock.

    Args:
        state (MarketState): Current regional stocks; the input is never mutated.
        flows (Iterable[ExternalTradeFlow]): Current-month boundary movements.

    Returns:
        MarketState: New finite nonnegative inventories; no partial exports or credits.
    """
    snapshot = MarketSnapshot.model_validate(state.snapshot.model_dump(warnings=False))
    records = validate_external_trades(flows)
    quantities = {
        (item.region, item.commodity): item.quantity_tonnes
        for item in snapshot.inventories
    }
    for flow in sorted(records, key=lambda item: not item.is_import):
        key = (flow.region, flow.commodity)
        context = f"{flow.region.value}/{flow.commodity.value} {flow.month}"
        if flow.month != snapshot.month or key not in quantities:
            raise ExternalTradeError(
                f"{context}: missing inventory or mismatched month"
            )
        starting = quantities[key]
        if not flow.is_import and flow.quantity_tonnes > starting:
            raise ExternalTradeError(
                f"{context}: export {flow.quantity_tonnes:g} t exceeds available "
                f"{starting:g} t; shortfall {flow.quantity_tonnes - starting:g} t"
            )
        change = flow.quantity_tonnes if flow.is_import else -flow.quantity_tonnes
        try:
            quantities[key] = math.fsum((starting, change))
        except OverflowError as exc:
            raise ExternalTradeError(f"{context}: boundary inventory overflow") from exc
    return MarketState.from_snapshot(
        MarketSnapshot(
            month=snapshot.month,
            prices=snapshot.prices,
            inventories=tuple(
                Inventory(region=region, commodity=commodity, quantity_tonnes=value)
                for (region, commodity), value in sorted(quantities.items())
            ),
        )
    )
