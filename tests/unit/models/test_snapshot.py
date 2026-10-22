from datetime import date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketSnapshot,
    Region,
)
from soybean_sim.models.snapshot import MarketSnapshot as SnapshotImplementation


@pytest.fixture
def snapshot() -> MarketSnapshot:
    """Provide synthetic global prices and distinct regional inventory keys."""
    month = date(2021, 1, 1)
    return MarketSnapshot(
        month=month,
        prices=tuple(
            CommodityPrice(
                commodity=commodity,
                month=month,
                price_usd_per_tonne=400,
                data_source="synthetic snapshot fixture",
            )
            for commodity in Commodity
        ),
        inventories=(
            Inventory(
                region=Region.BRAZIL, commodity=Commodity.SOYBEANS, quantity_tonnes=5
            ),
            Inventory(
                region=Region.UNITED_STATES,
                commodity=Commodity.SOYBEANS,
                quantity_tonnes=0,
            ),
            Inventory(
                region=Region.BRAZIL,
                commodity=Commodity.SOYBEAN_MEAL,
                quantity_tonnes=2,
            ),
        ),
    )


def test_public_api_and_valid_snapshot(snapshot: MarketSnapshot) -> None:
    """Reuse existing models without multiplying global prices by region count."""
    assert MarketSnapshot is SnapshotImplementation
    assert len(snapshot.prices) == 3
    assert len(snapshot.inventories) == 3
    assert all(isinstance(price, CommodityPrice) for price in snapshot.prices)
    assert all(isinstance(stock, Inventory) for stock in snapshot.inventories)
    assert snapshot.inventories[1].quantity_tonnes == 0


@pytest.mark.parametrize(
    "month",
    [date(2021, 1, 2), date(2021, 1, 31), datetime(2021, 1, 1), "2021-01-01", None, 0],
)
def test_rejects_invalid_months(snapshot: MarketSnapshot, month: Any) -> None:
    """Require an explicit first-of-month date, consistent with CommodityPrice."""
    with pytest.raises(ValidationError, match="month"):
        MarketSnapshot.model_validate({**snapshot.model_dump(), "month": month})


def test_requires_price_data(snapshot: MarketSnapshot) -> None:
    """Reject snapshots with no global reference prices."""
    with pytest.raises(ValidationError, match="prices"):
        MarketSnapshot(
            month=snapshot.month, prices=(), inventories=snapshot.inventories
        )


@pytest.mark.parametrize("price_value", [400, 500])
def test_rejects_duplicate_prices(snapshot: MarketSnapshot, price_value: float) -> None:
    """Reject repeated commodities even when their prices agree."""
    duplicate = snapshot.prices[0].model_copy(
        update={"price_usd_per_tonne": price_value}
    )
    with pytest.raises(ValidationError, match="prices must be unique by commodity"):
        MarketSnapshot(
            month=snapshot.month,
            prices=(*snapshot.prices, duplicate),
            inventories=snapshot.inventories,
        )


def test_rejects_price_month_mismatch(snapshot: MarketSnapshot) -> None:
    """Reject prices that do not describe the observed simulation month."""
    wrong_month = snapshot.prices[0].model_copy(update={"month": date(2021, 2, 1)})
    with pytest.raises(
        ValidationError, match="prices must refer to the snapshot month"
    ):
        MarketSnapshot(
            month=snapshot.month,
            prices=(wrong_month, *snapshot.prices[1:]),
            inventories=snapshot.inventories,
        )


@pytest.mark.parametrize("quantity", [5, 10])
def test_rejects_duplicate_inventories(
    snapshot: MarketSnapshot, quantity: float
) -> None:
    """Reject repeated region/commodity keys regardless of the reported quantity."""
    duplicate = snapshot.inventories[0].model_copy(update={"quantity_tonnes": quantity})
    with pytest.raises(ValidationError, match="inventories must be unique"):
        MarketSnapshot(
            month=snapshot.month,
            prices=snapshot.prices,
            inventories=(*snapshot.inventories, duplicate),
        )


def test_allows_unreported_inventory(snapshot: MarketSnapshot) -> None:
    """Keep an empty inventory collection empty rather than inventing zero records."""
    observed = MarketSnapshot(
        month=snapshot.month, prices=snapshot.prices, inventories=()
    )
    assert observed.inventories == ()


def test_copies_mutable_collections(snapshot: MarketSnapshot) -> None:
    """Convert caller-owned lists to tuples so later list edits cannot change a step."""
    prices = list(snapshot.prices)
    inventories = list(snapshot.inventories)
    observed = MarketSnapshot.model_validate(
        {"month": snapshot.month, "prices": prices, "inventories": inventories}
    )
    prices.clear()
    inventories.clear()
    assert observed == snapshot
    assert isinstance(observed.prices, tuple)
    assert isinstance(observed.inventories, tuple)


def test_immutable_snapshot_and_nested_records(snapshot: MarketSnapshot) -> None:
    """Prevent changes to the observation month, prices, and regional quantities."""
    with pytest.raises(ValidationError, match="frozen_instance"):
        snapshot.month = date(2021, 2, 1)
    with pytest.raises(ValidationError, match="frozen_instance"):
        snapshot.prices[0].price_usd_per_tonne = 500
    with pytest.raises(ValidationError, match="frozen_instance"):
        snapshot.inventories[0].quantity_tonnes = 10


def test_serialization_and_extra_fields(snapshot: MarketSnapshot) -> None:
    """Round-trip observations and reject annual balances or other undeclared fields."""
    assert MarketSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    assert snapshot.model_dump(mode="json")["month"] == "2021-01-01"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        MarketSnapshot.model_validate({**snapshot.model_dump(), "balances": []})
