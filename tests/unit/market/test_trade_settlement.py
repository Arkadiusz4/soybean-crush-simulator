import math
from collections.abc import Mapping
from datetime import date, datetime
from itertools import permutations
from sys import float_info
from typing import cast

import pytest

from soybean_sim import market
from soybean_sim.market import (
    MarketState,
    TradeExecution,
    TradeSettlementError,
    _trade_settlement,
    settle_matched_trades,
)
from soybean_sim.models import Commodity, Inventory, MarketSnapshot, Region


def _stocks(state: MarketState) -> dict[tuple[Region, Commodity], float]:
    """Read stock quantities independently of inventory record ordering."""
    return {
        (item.region, item.commodity): item.quantity_tonnes
        for item in state.snapshot.inventories
    }


def _with_stocks(
    state: MarketState, updates: Mapping[tuple[Region, Commodity], float]
) -> MarketState:
    """Replace or add synthetic stocks without changing prices or the calendar month."""
    quantities = _stocks(state) | dict(updates)
    return MarketState.from_snapshot(
        MarketSnapshot(
            month=state.snapshot.month,
            prices=state.snapshot.prices,
            inventories=tuple(
                Inventory(region=region, commodity=commodity, quantity_tonnes=quantity)
                for (region, commodity), quantity in sorted(quantities.items())
            ),
        )
    )


@pytest.fixture
def trade(state: MarketState) -> TradeExecution:
    """Transfer three thousand synthetic soybean tonnes from Brazil to the US."""
    return TradeExecution(
        month=state.snapshot.month,
        seller_agent_id="seller-br",
        buyer_agent_id="buyer-us",
        source_region=Region.BRAZIL,
        destination_region=Region.UNITED_STATES,
        commodity=Commodity.SOYBEANS,
        quantity_tonnes=3000,
    )


def test_public_exports() -> None:
    """Expose the independent settlement function and its domain-specific error."""
    assert settle_matched_trades is _trade_settlement.settle_matched_trades
    assert TradeSettlementError is _trade_settlement.TradeSettlementError
    assert issubclass(TradeSettlementError, ValueError)
    assert {"settle_matched_trades", "TradeSettlementError"} <= set(market.__all__)


@pytest.mark.parametrize("quantity", [3000, 10000, 0.125])
def test_single_trade_preserves_month_prices_and_inputs(
    state: MarketState, trade: TradeExecution, quantity: float
) -> None:
    """Transfer exact matched quantities, leaving unrelated stocks and inputs intact."""
    execution = trade.model_copy(update={"quantity_tonnes": quantity})
    original_state = state.model_dump_json()
    original_trade = execution.model_dump_json()
    updated = settle_matched_trades(state, [execution])
    expected = _stocks(state)
    expected[Region.BRAZIL, Commodity.SOYBEANS] -= quantity
    expected[Region.UNITED_STATES, Commodity.SOYBEANS] += quantity
    assert _stocks(updated) == expected
    assert updated is not state
    assert updated.snapshot.month == state.snapshot.month
    assert updated.snapshot.prices == state.snapshot.prices
    assert state.model_dump_json() == original_state
    assert execution.model_dump_json() == original_trade


def test_multiple_sellers_share_starting_regional_stock(
    state: MarketState, trade: TradeExecution
) -> None:
    """Aggregate sales from separate agent IDs against their common regional stock."""
    second = trade.model_copy(
        update={"seller_agent_id": "another-seller-br", "quantity_tonnes": 4000}
    )
    updated = settle_matched_trades(state, [trade, second])
    assert _stocks(updated)[Region.BRAZIL, Commodity.SOYBEANS] == 3000
    assert _stocks(updated)[Region.UNITED_STATES, Commodity.SOYBEANS] == 7100


def test_region_can_buy_and_sell_with_sufficient_starting_stock(
    state: MarketState, trade: TradeExecution
) -> None:
    """Allow two-way transfers when each region can fund its exports independently."""
    returning = trade.model_copy(
        update={
            "seller_agent_id": "seller-us",
            "buyer_agent_id": "buyer-br",
            "source_region": Region.UNITED_STATES,
            "destination_region": Region.BRAZIL,
            "quantity_tonnes": 80,
        }
    )
    updated = settle_matched_trades(state, [trade, returning])
    assert _stocks(updated)[Region.BRAZIL, Commodity.SOYBEANS] == 7080
    assert _stocks(updated)[Region.UNITED_STATES, Commodity.SOYBEANS] == 3020


def test_incoming_trades_cannot_fund_outgoing_sales(
    state: MarketState, trade: TradeExecution
) -> None:
    """Reject the complete batch in every order if gross sales exceed starting stock."""
    initial = _with_stocks(
        state,
        {
            (Region.BRAZIL, Commodity.SOYBEANS): 100,
            (Region.ARGENTINA, Commodity.SOYBEANS): 100,
            (Region.EUROPEAN_UNION, Commodity.SOYBEANS): 0,
            (Region.UNITED_STATES, Commodity.SOYBEANS): 0,
        },
    )
    trades = [
        trade.model_copy(
            update={"destination_region": Region.EUROPEAN_UNION, "quantity_tonnes": 80}
        ),
        trade.model_copy(
            update={
                "seller_agent_id": "seller-ar",
                "buyer_agent_id": "buyer-br",
                "source_region": Region.ARGENTINA,
                "destination_region": Region.BRAZIL,
                "quantity_tonnes": 100,
            }
        ),
        trade.model_copy(update={"quantity_tonnes": 50}),
    ]
    original = initial.model_dump_json()
    for ordered in permutations(trades):
        with pytest.raises(
            TradeSettlementError,
            match=r"Insufficient starting inventory.*brazil/soybeans.*130.*100",
        ):
            settle_matched_trades(initial, iter(ordered))
    assert initial.model_dump_json() == original


def test_multiple_commodities_are_conserved(
    state: MarketState, trade: TradeExecution
) -> None:
    """Move each commodity independently and preserve its total regional quantity."""
    trades = [trade]
    trades.extend(
        trade.model_copy(
            update={
                "seller_agent_id": "seller-us",
                "buyer_agent_id": "buyer-br",
                "source_region": Region.UNITED_STATES,
                "destination_region": Region.BRAZIL,
                "commodity": commodity,
                "quantity_tonnes": quantity,
            }
        )
        for commodity, quantity in (
            (Commodity.SOYBEAN_MEAL, 2),
            (Commodity.SOYBEAN_OIL, 3),
        )
    )
    updated = settle_matched_trades(state, trades)
    assert _stocks(updated) == {
        (Region.BRAZIL, Commodity.SOYBEANS): 7000,
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL): 2,
        (Region.BRAZIL, Commodity.SOYBEAN_OIL): 3,
        (Region.UNITED_STATES, Commodity.SOYBEANS): 3100,
        (Region.UNITED_STATES, Commodity.SOYBEAN_MEAL): 3,
        (Region.UNITED_STATES, Commodity.SOYBEAN_OIL): 4,
    }
    for commodity in Commodity:
        before = math.fsum(
            item.quantity_tonnes
            for item in state.snapshot.inventories
            if item.commodity == commodity
        )
        after = math.fsum(
            item.quantity_tonnes
            for item in updated.snapshot.inventories
            if item.commodity == commodity
        )
        assert after == pytest.approx(before, rel=1e-12, abs=1e-9)


def test_trade_and_inventory_order_do_not_change_results(
    state: MarketState, trade: TradeExecution
) -> None:
    """Return the same sorted inventories for reordered trades and source records."""
    trades = [
        trade.model_copy(
            update={"buyer_agent_id": f"buyer-{index}", "quantity_tonnes": quantity}
        )
        for index, quantity in enumerate((2500, 1250, 0.125))
    ]
    original_state = state.model_dump_json()
    original_trades = [item.model_dump_json() for item in trades]
    expected = settle_matched_trades(state, trades)
    unordered = state.model_copy(
        update={
            "snapshot": state.snapshot.model_copy(
                update={"inventories": tuple(reversed(state.snapshot.inventories))}
            )
        }
    )
    for ordered in permutations(trades):
        assert settle_matched_trades(unordered, iter(ordered)) == expected
    keys = [(item.region, item.commodity) for item in expected.snapshot.inventories]
    assert keys == sorted(keys)
    assert state.model_dump_json() == original_state
    assert [item.model_dump_json() for item in trades] == original_trades


@pytest.mark.parametrize("quantity", [10001, math.nextafter(10000, math.inf)])
def test_oversized_trades_are_rejected_without_clipping(
    state: MarketState, trade: TradeExecution, quantity: float
) -> None:
    """Reject small overallocations without changing matched amounts."""
    with pytest.raises(TradeSettlementError, match="Insufficient starting inventory"):
        settle_matched_trades(
            state, [trade.model_copy(update={"quantity_tonnes": quantity})]
        )


@pytest.mark.parametrize(
    ("region", "role"),
    [(Region.BRAZIL, "source"), (Region.UNITED_STATES, "destination")],
)
def test_missing_inventory_is_not_created(
    state: MarketState, trade: TradeExecution, region: Region, role: str
) -> None:
    """Require both transfer endpoints to have explicit inventory records."""
    snapshot = MarketSnapshot(
        month=state.snapshot.month,
        prices=state.snapshot.prices,
        inventories=tuple(
            item
            for item in state.snapshot.inventories
            if (item.region, item.commodity) != (region, Commodity.SOYBEANS)
        ),
    )
    with pytest.raises(TradeSettlementError, match=f"Missing {role} inventory"):
        settle_matched_trades(MarketState.from_snapshot(snapshot), [trade])


@pytest.mark.parametrize("duplicate", ["same", "copy", "trimmed_ids"])
def test_identical_trades_are_rejected(
    state: MarketState, trade: TradeExecution, duplicate: str
) -> None:
    """Detect repeated execution records after normalizing participant identifiers."""
    copies = {
        "same": trade,
        "copy": trade.model_copy(),
        "trimmed_ids": trade.model_copy(update={"seller_agent_id": " seller-br "}),
    }
    original = state.model_dump_json()
    with pytest.raises(TradeSettlementError, match="Duplicate trade"):
        settle_matched_trades(state, [trade, copies[duplicate]])
    assert state.model_dump_json() == original


def test_trade_month_must_match_state(
    state: MarketState, trade: TradeExecution
) -> None:
    """Reject a valid execution record belonging to another calendar month."""
    with pytest.raises(TradeSettlementError, match=r"Trade month.*market month"):
        settle_matched_trades(
            state, [trade.model_copy(update={"month": date(2021, 2, 1)})]
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"month": date(2021, 1, 2)},
        {"month": datetime(2021, 1, 1)},
        {"month": "2021-01-01"},
        {"quantity_tonnes": 0},
        {"quantity_tonnes": -1},
        {"quantity_tonnes": float("nan")},
        {"quantity_tonnes": float("inf")},
        {"quantity_tonnes": True},
        {"quantity_tonnes": "3"},
        {"seller_agent_id": " "},
        {"buyer_agent_id": "seller-br"},
        {"destination_region": Region.BRAZIL},
        {"commodity": "unknown"},
    ],
)
def test_unchecked_trades_are_revalidated(
    state: MarketState, trade: TradeExecution, changes: dict[str, object]
) -> None:
    """Wrap trade validation failures, including unchecked model copies."""
    with pytest.raises(TradeSettlementError, match="Invalid trade"):
        settle_matched_trades(state, [trade.model_copy(update=changes)])


@pytest.mark.parametrize("defect", ["month", "duplicate_stock", "quantity", "price"])
def test_initial_state_is_revalidated_even_for_empty_trades(
    state: MarketState, defect: str
) -> None:
    """Preserve nested state invariants before accepting any settlement batch."""
    changes = {
        "month": {"month": date(2021, 1, 2)},
        "duplicate_stock": {
            "inventories": (*state.snapshot.inventories, state.snapshot.inventories[0])
        },
        "quantity": {
            "inventories": (
                state.snapshot.inventories[0].model_copy(
                    update={"quantity_tonnes": -1}
                ),
            )
        },
        "price": {
            "prices": (
                state.snapshot.prices[0].model_copy(
                    update={"price_usd_per_tonne": float("inf")}
                ),
            )
        },
    }
    invalid = state.model_copy(
        update={"snapshot": state.snapshot.model_copy(update=changes[defect])}
    )
    with pytest.raises(TradeSettlementError, match="Invalid market state"):
        settle_matched_trades(invalid, [])


def test_empty_trades_are_a_noop(state: MarketState) -> None:
    """Return a new, equal state without altering inventory order for an empty batch."""
    result = settle_matched_trades(state, iter(()))
    assert result == state
    assert result is not state


@pytest.mark.parametrize("kind", ["state", "trade"])
def test_wrong_input_types_are_domain_errors(state: MarketState, kind: str) -> None:
    """Reject arbitrary mappings passed in place of typed models."""
    if kind == "state":
        with pytest.raises(TradeSettlementError, match="state must be a MarketState"):
            settle_matched_trades(cast(MarketState, {}), [])
    else:
        with pytest.raises(TradeSettlementError, match="trades must contain"):
            settle_matched_trades(state, [cast(TradeExecution, {})])


def test_fractional_matched_quantities_leave_no_negative_residual(
    state: MarketState, trade: TradeExecution
) -> None:
    """Settle fractional fills using their original unrounded executable quantities."""
    initial = _with_stocks(state, {(Region.BRAZIL, Commodity.SOYBEANS): 0.3})
    trades = [
        trade.model_copy(
            update={"buyer_agent_id": f"buyer-{index}", "quantity_tonnes": quantity}
        )
        for index, quantity in enumerate((0.1, 0.3 - 0.1))
    ]
    updated = settle_matched_trades(initial, trades)
    assert _stocks(updated)[Region.BRAZIL, Commodity.SOYBEANS] == 0
    assert _stocks(updated)[Region.UNITED_STATES, Commodity.SOYBEANS] == pytest.approx(
        100.3
    )


def test_large_and_small_transfers_use_stable_summation(
    state: MarketState, trade: TradeExecution
) -> None:
    """Retain small transfers alongside large ones, independently of batch ordering."""
    initial = _with_stocks(
        state,
        {
            (Region.BRAZIL, Commodity.SOYBEANS): 2e16,
            (Region.UNITED_STATES, Commodity.SOYBEANS): 0,
        },
    )
    trades = [
        trade.model_copy(
            update={"buyer_agent_id": f"buyer-{index}", "quantity_tonnes": quantity}
        )
        for index, quantity in enumerate((1e16, 1, 1, 1))
    ]
    expected_export = math.fsum(item.quantity_tonnes for item in trades)
    for ordered in permutations(trades):
        updated = settle_matched_trades(initial, ordered)
        assert (
            _stocks(updated)[Region.BRAZIL, Commodity.SOYBEANS]
            == 2e16 - expected_export
        )
        assert (
            _stocks(updated)[Region.UNITED_STATES, Commodity.SOYBEANS]
            == expected_export
        )


@pytest.mark.parametrize("stage", ["outgoing", "incoming", "ending"])
def test_overflow_is_a_domain_error(
    state: MarketState, trade: TradeExecution, stage: str
) -> None:
    """Reject aggregate or inventory overflow without returning partial results."""
    initial = _with_stocks(
        state,
        {
            (Region.BRAZIL, Commodity.SOYBEANS): float_info.max,
            (Region.ARGENTINA, Commodity.SOYBEANS): float_info.max,
            (Region.UNITED_STATES, Commodity.SOYBEANS): float_info.max
            if stage == "ending"
            else 0,
        },
    )
    large = trade.model_copy(update={"quantity_tonnes": float_info.max})
    trades = [large]
    if stage == "outgoing":
        trades.append(large.model_copy(update={"seller_agent_id": "other-seller"}))
    elif stage == "incoming":
        trades.append(
            large.model_copy(
                update={
                    "seller_agent_id": "seller-ar",
                    "source_region": Region.ARGENTINA,
                }
            )
        )
    original = initial.model_dump_json()
    with pytest.raises(TradeSettlementError, match="overflow"):
        settle_matched_trades(initial, trades)
    assert initial.model_dump_json() == original


def test_large_balanced_transfers_do_not_overflow(
    state: MarketState, trade: TradeExecution
) -> None:
    """Subtract outgoing stock before adding incoming quantities of similar size."""
    initial = _with_stocks(
        state,
        {
            (Region.BRAZIL, Commodity.SOYBEANS): float_info.max,
            (Region.UNITED_STATES, Commodity.SOYBEANS): float_info.max,
        },
    )
    outgoing = trade.model_copy(update={"quantity_tonnes": float_info.max})
    returning = outgoing.model_copy(
        update={
            "source_region": Region.UNITED_STATES,
            "destination_region": Region.BRAZIL,
        }
    )
    assert settle_matched_trades(initial, [outgoing, returning]) == initial
