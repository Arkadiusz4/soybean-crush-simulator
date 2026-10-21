import pytest

from soybean_sim.data import (
    InventoryInitializationError,
    build_initial_inventories,
    inventory,
)
from soybean_sim.models import Commodity, Inventory, MarketBalance, Region


@pytest.fixture
def balances() -> list[MarketBalance]:
    """Provide synthetic beginning stocks with unrelated quantities left unknown."""
    return [
        MarketBalance(
            region=region,
            commodity=commodity,
            marketing_year="2020/21",
            production_tonnes=None,
            imports_tonnes=None,
            exports_tonnes=None,
            domestic_consumption_tonnes=None,
            beginning_stocks_tonnes=quantity,
            ending_stocks_tonnes=999,
        )
        for region, commodity, quantity in (
            (Region.UNITED_STATES, Commodity.SOYBEANS, 1234.5),
            (Region.BRAZIL, Commodity.SOYBEAN_MEAL, 10.25),
            (Region.ARGENTINA, Commodity.SOYBEAN_OIL, 0),
            (Region.EUROPEAN_UNION, Commodity.SOYBEANS, 1000000),
            (Region.BRAZIL, Commodity.SOYBEANS, 50),
        )
    ]


def test_public_exports() -> None:
    """Expose the builder and domain exception from the data package and module."""
    assert build_initial_inventories is inventory.build_initial_inventories
    assert InventoryInitializationError is inventory.InventoryInitializationError
    assert issubclass(InventoryInitializationError, ValueError)


def test_builds_one_inventory(balances: list[MarketBalance]) -> None:
    """Map beginning stocks directly without using ending stocks or changing units."""
    assert build_initial_inventories([balances[0]]) == (
        Inventory(
            region=Region.UNITED_STATES,
            commodity=Commodity.SOYBEANS,
            quantity_tonnes=1234.5,
        ),
    )


def test_builds_sorted_inventories_without_mutation(
    balances: list[MarketBalance],
) -> None:
    """Keep all pairs and quantities in deterministic region/commodity order."""
    original_records = [balance.model_dump() for balance in balances]
    inventories = build_initial_inventories(balances)
    assert isinstance(inventories, tuple)
    assert [
        (item.region, item.commodity, item.quantity_tonnes) for item in inventories
    ] == [
        (Region.ARGENTINA, Commodity.SOYBEAN_OIL, 0),
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL, 10.25),
        (Region.BRAZIL, Commodity.SOYBEANS, 50),
        (Region.EUROPEAN_UNION, Commodity.SOYBEANS, 1000000),
        (Region.UNITED_STATES, Commodity.SOYBEANS, 1234.5),
    ]
    assert build_initial_inventories(reversed(balances)) == inventories
    assert [balance.model_dump() for balance in balances] == original_records


def test_preserves_zero_stocks(balances: list[MarketBalance]) -> None:
    """Treat known zero beginning stocks as a valid empty inventory."""
    inventories = build_initial_inventories([balances[2]])
    assert len(inventories) == 1
    assert inventories[0].quantity_tonnes == 0


@pytest.mark.parametrize("ending_stocks", [None, 0, 50])
def test_rejects_missing_beginning_stocks(
    balances: list[MarketBalance], ending_stocks: float | None
) -> None:
    """Report the affected balance without falling back to available ending stocks."""
    balance = balances[0].model_copy(
        update={
            "beginning_stocks_tonnes": None,
            "ending_stocks_tonnes": ending_stocks,
        }
    )
    with pytest.raises(InventoryInitializationError) as error:
        build_initial_inventories([balance])
    message = str(error.value)
    assert "beginning_stocks_tonnes" in message
    assert "united_states/soybeans" in message
    assert "2020/21" in message


@pytest.mark.parametrize(
    "duplicate_kind", ["same_instance", "equal", "different_stocks", "different_year"]
)
def test_rejects_duplicate_pairs(
    balances: list[MarketBalance], duplicate_kind: str
) -> None:
    """Reject every repeated pair, including equal records and other marketing years."""
    balance = balances[0]
    duplicates = {
        "same_instance": balance,
        "equal": balance.model_copy(),
        "different_stocks": balance.model_copy(update={"beginning_stocks_tonnes": 1}),
        "different_year": balance.model_copy(update={"marketing_year": "2021/22"}),
    }
    with pytest.raises(
        InventoryInitializationError,
        match="Duplicate balance for united_states/soybeans",
    ):
        build_initial_inventories([balance, duplicates[duplicate_kind]])


def test_accepts_one_pass_iterables(balances: list[MarketBalance]) -> None:
    """Consume a generator once and return the same records as a reusable list."""
    records = (balance for balance in balances)
    assert build_initial_inventories(records) == build_initial_inventories(balances)


def test_empty_input() -> None:
    """Return an empty tuple when no balances are supplied."""
    assert build_initial_inventories(iter(())) == ()
