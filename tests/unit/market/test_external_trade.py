from datetime import date

import pytest
from pydantic import ValidationError

from soybean_sim.market import (
    ExternalMarket,
    ExternalTradeError,
    ExternalTradeFlow,
    MarketState,
    settle_external_trades,
    validate_external_trades,
)
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketSnapshot,
    Region,
)


@pytest.fixture
def state() -> MarketState:
    """Provide a small explicitly synthetic market with known regional stocks.

    Returns:
        MarketState: Ten tonnes of each commodity in Argentina.
    """
    month = date(2025, 1, 1)
    return MarketState.from_snapshot(
        MarketSnapshot(
            month=month,
            prices=(
                CommodityPrice(
                    commodity=Commodity.SOYBEANS,
                    month=month,
                    price_usd_per_tonne=100,
                    data_source="synthetic",
                ),
            ),
            inventories=tuple(
                Inventory(
                    region=Region.ARGENTINA, commodity=commodity, quantity_tonnes=10
                )
                for commodity in Commodity
            ),
        )
    )


def transfer(quantity: float = 5, *, importing: bool = True) -> ExternalTradeFlow:
    """Construct a test transfer with explicit regional and boundary endpoints.

    Args:
        quantity (float): Physical tonnes in the synthetic movement.
        importing (bool): Select boundary-to-Argentina or the reverse direction.

    Returns:
        ExternalTradeFlow: A January soybean transfer.
    """
    return ExternalTradeFlow(
        month=date(2025, 1, 1),
        commodity=Commodity.SOYBEANS,
        source=ExternalMarket.CLEARING_BOUNDARY if importing else Region.ARGENTINA,
        destination=Region.ARGENTINA if importing else ExternalMarket.CLEARING_BOUNDARY,
        quantity_tonnes=quantity,
    )


@pytest.mark.parametrize("importing", [False, True])
@pytest.mark.parametrize("quantity", [0, 5, 10])
def test_boundary_conservation_and_immutability(
    state: MarketState,
    importing: bool,
    quantity: float,
) -> None:
    """Conserve regional and external mass without changing source stocks."""
    record = transfer(quantity, importing=importing)
    before = state.model_dump_json()
    result = settle_external_trades(state, (record,))
    change = quantity if importing else -quantity
    assert result.snapshot.inventories[-1].commodity == Commodity.SOYBEANS
    assert result.snapshot.inventories[-1].quantity_tonnes == 10 + change
    external_change = -change
    assert (result.snapshot.inventories[-1].quantity_tonnes - 10) + external_change == 0
    assert state.model_dump_json() == before
    assert result == settle_external_trades(state, (record,))
    assert ExternalTradeFlow.model_validate_json(record.model_dump_json()) == record
    with pytest.raises(ValidationError, match="frozen"):
        record.quantity_tonnes = 1


@pytest.mark.parametrize("quantity", [-1, float("nan"), float("inf"), True, "5"])
def test_invalid_quantities(quantity: object) -> None:
    """Reject negative, nonfinite, and coerced boundary quantities."""
    with pytest.raises(ValidationError):
        transfer(quantity)


@pytest.mark.parametrize(
    "changes",
    [
        {"source": Region.BRAZIL, "destination": Region.ARGENTINA},
        {
            "source": ExternalMarket.CLEARING_BOUNDARY,
            "destination": ExternalMarket.CLEARING_BOUNDARY,
        },
        {"source": "unknown"},
        {"commodity": "unknown"},
        {"month": date(2025, 1, 2)},
        {"month": "2025-01-01"},
        {"unexpected": 1},
    ],
)
def test_invalid_contracts(changes: dict[str, object]) -> None:
    """Require a monthly transfer with exactly one known regional endpoint."""
    with pytest.raises(ValidationError):
        ExternalTradeFlow.model_validate({**transfer().model_dump(), **changes})


def test_unavailable_exports_are_atomic(state: MarketState) -> None:
    """Reject unavailable exports without changing stocks or crediting unknown regions.

    Args:
        state (MarketState): Synthetic regional inventories.

    Returns:
        None: Confirms invalid withdrawals are atomic.
    """
    before = state.model_dump_json()
    with pytest.raises(ExternalTradeError, match="shortfall 5"):
        settle_external_trades(state, (transfer(1), transfer(16, importing=False)))
    assert state.model_dump_json() == before
    with pytest.raises(ExternalTradeError, match="missing inventory"):
        settle_external_trades(
            state, (transfer().model_copy(update={"destination": Region.BRAZIL}),)
        )
    with pytest.raises(ExternalTradeError, match="mismatched month"):
        settle_external_trades(
            state, (transfer().model_copy(update={"month": date(2025, 2, 1)}),)
        )


def test_schedule_duplicates_and_order() -> None:
    """Keep deterministic ordering and reject repeated transfer keys."""
    first, second = transfer(2), transfer(1, importing=False)
    assert validate_external_trades((first, second)) == validate_external_trades(
        (second, first)
    )
    for duplicate in (first, transfer(3)):
        with pytest.raises(ExternalTradeError, match="Duplicate"):
            validate_external_trades((first, duplicate))
    with pytest.raises(ExternalTradeError, match="Expected"):
        validate_external_trades((object(),))
    with pytest.raises(ExternalTradeError, match="Invalid"):
        validate_external_trades((first.model_copy(update={"quantity_tonnes": -1}),))
