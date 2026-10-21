from datetime import date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from soybean_sim.data import (
    InventoryInitializationError,
    MarketDataSelectionError,
    build_simulation_input,
    builder,
)
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketBalance,
    Region,
    SimulationInput,
)


@pytest.fixture
def balances() -> list[MarketBalance]:
    """Provide synthetic annual balances, including known zero and unknown flows."""
    return [
        MarketBalance(
            region=region,
            commodity=commodity,
            marketing_year="2020/21",
            production_tonnes=1000,
            imports_tonnes=None,
            exports_tonnes=None,
            domestic_consumption_tonnes=900,
            beginning_stocks_tonnes=stocks,
            ending_stocks_tonnes=500,
        )
        for region, commodity, stocks in (
            (Region.UNITED_STATES, Commodity.SOYBEANS, 10.25),
            (Region.UNITED_STATES, Commodity.SOYBEAN_MEAL, 0),
            (Region.BRAZIL, Commodity.SOYBEANS, 20),
            (Region.BRAZIL, Commodity.SOYBEAN_MEAL, 30),
        )
    ]


@pytest.fixture
def prices() -> list[CommodityPrice]:
    """Provide one synthetic global reference price per commodity for January."""
    return [
        CommodityPrice(
            commodity=commodity,
            month=date(2021, 1, 1),
            price_usd_per_tonne=price,
            data_source="synthetic assembly fixture",
        )
        for commodity, price in (
            (Commodity.SOYBEANS, 400),
            (Commodity.SOYBEAN_MEAL, 350),
        )
    ]


@pytest.fixture
def options() -> dict[str, Any]:
    """Request all fixture pairs with explicit, independent reporting periods."""
    return {
        "marketing_year": "2020/21",
        "price_month": date(2021, 1, 1),
        "regions": [Region.UNITED_STATES, Region.BRAZIL],
        "commodities": [Commodity.SOYBEANS, Commodity.SOYBEAN_MEAL],
    }


def test_public_exports() -> None:
    """Expose the assembly function through the data package and its module."""
    assert build_simulation_input is builder.build_simulation_input


def test_assembles_complete_input_without_mutation(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Keep annual quantities, global prices, and beginning stocks in sorted tuples."""
    original_balances = [balance.model_dump() for balance in balances]
    original_prices = [price.model_dump() for price in prices]
    initial = build_simulation_input(balances, prices, **options)

    assert initial.balances == tuple(
        sorted(balances, key=lambda balance: (balance.region, balance.commodity))
    )
    assert initial.prices == tuple(sorted(prices, key=lambda price: price.commodity))
    assert initial.inventories == (
        Inventory(
            region=Region.BRAZIL, commodity=Commodity.SOYBEAN_MEAL, quantity_tonnes=30
        ),
        Inventory(
            region=Region.BRAZIL, commodity=Commodity.SOYBEANS, quantity_tonnes=20
        ),
        Inventory(
            region=Region.UNITED_STATES,
            commodity=Commodity.SOYBEAN_MEAL,
            quantity_tonnes=0,
        ),
        Inventory(
            region=Region.UNITED_STATES,
            commodity=Commodity.SOYBEANS,
            quantity_tonnes=10.25,
        ),
    )
    assert len(initial.prices) == 2  # Global prices are shared across both regions.
    assert all(balance.production_tonnes == 1000 for balance in initial.balances)
    assert all(balance.imports_tonnes is None for balance in initial.balances)
    assert [balance.model_dump() for balance in balances] == original_balances
    assert [price.model_dump() for price in prices] == original_prices
    assert (
        build_simulation_input(reversed(balances), reversed(prices), **options)
        == initial
    )


def test_filters_before_initialization_with_independent_periods(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Ignore other periods and pairs, including balances with unavailable stocks."""
    options.update(
        regions=[Region.BRAZIL],
        commodities=[Commodity.SOYBEANS],
        price_month=date(2050, 6, 1),
    )
    selected_balance = balances[2]
    selected_price = prices[0].model_copy(update={"month": date(2050, 6, 1)})
    unselected_balances = [
        balance.model_copy(update={"beginning_stocks_tonnes": None})
        for balance in balances
        if balance != selected_balance
    ]
    other_year = selected_balance.model_copy(
        update={"marketing_year": "2021/22", "beginning_stocks_tonnes": None}
    )
    initial = build_simulation_input(
        [other_year, *unselected_balances, selected_balance],
        [*prices, selected_price],
        **options,
    )
    assert initial.balances == (selected_balance,)
    assert initial.prices == (selected_price,)
    assert initial.inventories[0].quantity_tonnes == 20


def test_accepts_one_pass_iterables(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Consume source records and selection generators in a single pass."""
    expected = build_simulation_input(balances, prices, **options)
    options.update(
        regions=iter(options["regions"]),
        commodities=iter(options["commodities"]),
    )
    assert build_simulation_input(iter(balances), iter(prices), **options) == expected


def test_missing_balances_preserve_selection_error(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Identify missing requested pairs through the existing selection exception."""
    with pytest.raises(
        MarketDataSelectionError, match=r"Missing balances.*united_states/soybeans"
    ):
        build_simulation_input(balances[1:], prices, **options)


def test_missing_prices_preserve_selection_error(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Require a global price per requested commodity without wrapping the error."""
    with pytest.raises(
        MarketDataSelectionError, match=r"Missing global prices.*soybeans"
    ):
        build_simulation_input(balances, prices[1:], **options)


def test_missing_stocks_preserve_inventory_error(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Reject missing stocks after selection rather than using ending stocks."""
    balances[0] = balances[0].model_copy(update={"beginning_stocks_tonnes": None})
    with pytest.raises(
        InventoryInitializationError,
        match=r"Missing beginning_stocks_tonnes.*united_states/soybeans",
    ):
        build_simulation_input(balances, prices, **options)


@pytest.mark.parametrize(
    "changes",
    [
        {"marketing_year": ""},
        {"marketing_year": None},
        {"price_month": date(2021, 1, 2)},
        {"price_month": "2021-01-01"},
        {"price_month": datetime(2021, 1, 1)},
        {"regions": []},
        {"commodities": []},
        {"regions": ["unknown"]},
        {"commodities": ["unknown"]},
    ],
)
def test_invalid_selections_preserve_selection_error(
    balances: list[MarketBalance],
    prices: list[CommodityPrice],
    options: dict[str, Any],
    changes: dict[str, Any],
) -> None:
    """Delegate argument validation to selection without introducing another policy."""
    options.update(changes)
    with pytest.raises(MarketDataSelectionError):
        build_simulation_input(balances, prices, **options)


@pytest.mark.parametrize("record_type", ["balance", "price"])
def test_conflicting_records_preserve_selection_error(
    balances: list[MarketBalance],
    prices: list[CommodityPrice],
    options: dict[str, Any],
    record_type: str,
) -> None:
    """Reject conflicting records before inventory creation and model validation."""
    if record_type == "balance":
        balances.append(balances[0].model_copy(update={"production_tonnes": 1}))
    else:
        prices.append(prices[0].model_copy(update={"price_usd_per_tonne": 1}))
    with pytest.raises(MarketDataSelectionError, match="Conflicting"):
        build_simulation_input(balances, prices, **options)


def test_equal_duplicates_are_selected_once(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Let selection collapse equal records before the inventory duplicate guard."""
    expected = build_simulation_input(balances, prices, **options)
    balances.append(balances[0].model_copy())
    prices.append(prices[0].model_copy())
    assert build_simulation_input(balances, prices, **options) == expected


def test_result_retains_simulation_input_contract(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Keep the model's immutability and JSON serialization contract."""
    initial = build_simulation_input(balances, prices, **options)
    assert type(initial) is SimulationInput
    assert SimulationInput.model_validate_json(initial.model_dump_json()) == initial
    with pytest.raises(ValidationError, match="frozen_instance"):
        initial.balances = ()
