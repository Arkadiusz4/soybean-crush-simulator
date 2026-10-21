from datetime import date, datetime
from typing import Any

import pytest

from soybean_sim.data import MarketDataSelectionError, select_market_data, selection
from soybean_sim.models import Commodity, CommodityPrice, MarketBalance, Region


@pytest.fixture
def balances() -> list[MarketBalance]:
    """Provide synthetic balances with known zeroes and explicitly unknown exports."""
    return [
        MarketBalance(
            region=region,
            commodity=commodity,
            marketing_year="2020/21",
            production_tonnes=100,
            imports_tonnes=0,
            exports_tonnes=None,
            domestic_consumption_tonnes=90,
            beginning_stocks_tonnes=10,
            ending_stocks_tonnes=20,
        )
        for region in (Region.UNITED_STATES, Region.BRAZIL)
        for commodity in (Commodity.SOYBEANS, Commodity.SOYBEAN_MEAL)
    ]


@pytest.fixture
def prices() -> list[CommodityPrice]:
    """Provide one synthetic global reference price per requested commodity."""
    return [
        CommodityPrice(
            commodity=commodity,
            month=date(2021, 1, 1),
            price_usd_per_tonne=400,
            data_source="synthetic selection fixture",
        )
        for commodity in (Commodity.SOYBEANS, Commodity.SOYBEAN_MEAL)
    ]


@pytest.fixture
def options() -> dict[str, Any]:
    """Request a complete two-region selection using independent reporting periods."""
    return {
        "marketing_year": "2020/21",
        "price_month": date(2021, 1, 1),
        "regions": [Region.UNITED_STATES, Region.BRAZIL],
        "commodities": [Commodity.SOYBEANS, Commodity.SOYBEAN_MEAL],
    }


def test_public_exports() -> None:
    """Expose the selector and its domain exception through the data package."""
    assert select_market_data is selection.select_market_data
    assert MarketDataSelectionError is selection.MarketDataSelectionError
    assert issubclass(MarketDataSelectionError, ValueError)


def test_selects_complete_deterministic_records_without_mutation(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Select sorted original records without altering input lists or missing values."""
    original_balances = list(balances)
    original_prices = list(prices)
    selected_balances, selected_prices = select_market_data(balances, prices, **options)
    assert len(selected_balances) == 4
    assert (
        len(selected_prices) == 2
    )  # Global prices are not multiplied by region count.
    assert isinstance(selected_balances, tuple)
    assert isinstance(selected_prices, tuple)
    assert balances == original_balances
    assert prices == original_prices
    assert all(
        any(item is original for original in balances) for item in selected_balances
    )
    assert all(any(item is original for original in prices) for item in selected_prices)
    assert all(item.exports_tonnes is None for item in selected_balances)
    assert all(item.imports_tonnes == 0 for item in selected_balances)
    assert selected_balances == tuple(
        sorted(balances, key=lambda x: (x.region, x.commodity))
    )
    assert selected_prices == tuple(sorted(prices, key=lambda x: x.commodity))
    assert select_market_data(reversed(balances), reversed(prices), **options) == (
        selected_balances,
        selected_prices,
    )


def test_selects_only_the_requested_subset(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Exclude unrequested regions and commodities without requiring their coverage."""
    options.update(regions=[Region.BRAZIL], commodities=[Commodity.SOYBEANS])
    selected_balances, selected_prices = select_market_data(balances, prices, **options)
    assert len(selected_balances) == len(selected_prices) == 1
    assert selected_balances[0].region is Region.BRAZIL
    assert selected_balances[0].commodity is Commodity.SOYBEANS
    assert selected_prices[0].commodity is Commodity.SOYBEANS


def test_ignores_conflicts_outside_selected_scope(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Filter other years, months, regions, and commodities before duplicate checks."""
    options.update(regions=[Region.UNITED_STATES], commodities=[Commodity.SOYBEANS])
    expected = select_market_data(balances, prices, **options)
    for production in (200, 300):
        for fields in (
            {"marketing_year": "2021"},
            {"region": Region.ARGENTINA},
            {"commodity": Commodity.SOYBEAN_OIL},
        ):
            balances.append(
                balances[0].model_copy(
                    update={
                        **fields,
                        "production_tonnes": production,
                    }
                )
            )
        prices.append(
            prices[0].model_copy(
                update={
                    "month": date(2021, 2, 1),
                    "price_usd_per_tonne": production,
                }
            )
        )
        prices.append(
            prices[0].model_copy(
                update={
                    "commodity": Commodity.SOYBEAN_OIL,
                    "price_usd_per_tonne": production,
                }
            )
        )
    assert select_market_data(balances, prices, **options) == expected


@pytest.mark.parametrize("year_label", ["2020/21", "MY 2020/2021", "0001"])
def test_labels_and_months_are_independent(
    balances: list[MarketBalance],
    prices: list[CommodityPrice],
    options: dict[str, Any],
    year_label: str,
) -> None:
    """Match source labels without deriving calendar dates or monthly quantities."""
    annual_records = [
        item.model_copy(update={"marketing_year": year_label}) for item in balances
    ]
    monthly_records = [
        item.model_copy(update={"month": date(2050, 6, 1)}) for item in prices
    ]
    options.update(marketing_year=f" {year_label} ", price_month=date(2050, 6, 1))
    selected_balances, selected_prices = select_market_data(
        annual_records, monthly_records, **options
    )
    assert {item.marketing_year for item in selected_balances} == {year_label}
    assert {item.production_tonnes for item in selected_balances} == {100}
    assert {item.month for item in selected_prices} == {date(2050, 6, 1)}


def test_identical_duplicates_count_once(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Collapse equal records while keeping their original first instances."""
    expected = select_market_data(balances, prices, **options)
    balances.append(balances[0].model_copy())
    prices.append(prices[0].model_copy())
    assert select_market_data(balances, prices, **options) == expected


@pytest.mark.parametrize("changes", [{"production_tonnes": 101}, {"exports_tonnes": 0}])
def test_conflicting_balances_identify_the_requested_key(
    balances: list[MarketBalance],
    prices: list[CommodityPrice],
    options: dict[str, Any],
    changes: dict[str, Any],
) -> None:
    """Distinguish conflicting quantities, including unknown versus known zero."""
    balances.append(balances[0].model_copy(update=changes))
    with pytest.raises(MarketDataSelectionError) as caught:
        select_market_data(balances, prices, **options)
    message = str(caught.value)
    assert "Conflicting balances" in message
    assert "united_states/soybeans" in message
    assert "2020/21" in message


@pytest.mark.parametrize(
    "changes", [{"price_usd_per_tonne": 401}, {"data_source": "another source"}]
)
def test_conflicting_prices_include_provenance(
    balances: list[MarketBalance],
    prices: list[CommodityPrice],
    options: dict[str, Any],
    changes: dict[str, Any],
) -> None:
    """Treat different prices or source labels as conflicting records for one key."""
    prices.append(prices[0].model_copy(update=changes))
    with pytest.raises(MarketDataSelectionError) as caught:
        select_market_data(balances, prices, **options)
    assert "Conflicting prices for soybeans in 2021-01-01" in str(caught.value)


def test_every_explicit_region_commodity_pair_is_required(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Name all absent requested pairs even if the historical dataset lacks them."""
    options["regions"].append(Region.EUROPEAN_UNION)
    with pytest.raises(MarketDataSelectionError) as caught:
        select_market_data(balances, prices, **options)
    assert str(caught.value) == (
        "Missing balances for marketing year '2020/21': "
        "european_union/soybean_meal, european_union/soybeans"
    )


@pytest.mark.parametrize("missing", ["balances", "prices"])
def test_empty_input_records_report_missing_coverage(
    balances: list[MarketBalance],
    prices: list[CommodityPrice],
    options: dict[str, Any],
    missing: str,
) -> None:
    """Reject empty historical collections when the requested selection is nonempty."""
    if missing == "balances":
        balances.clear()
    else:
        prices.clear()
    with pytest.raises(MarketDataSelectionError, match="Missing"):
        select_market_data(balances, prices, **options)


@pytest.mark.parametrize(
    ("field", "period", "expected"),
    [
        ("marketing_year", "2021", "Missing balances"),
        ("price_month", date(2021, 2, 1), "Missing global prices for 2021-02-01"),
    ],
)
def test_other_periods_cannot_substitute_for_requested_data(
    balances: list[MarketBalance],
    prices: list[CommodityPrice],
    options: dict[str, Any],
    field: str,
    period: Any,
    expected: str,
) -> None:
    """Require exact period matches rather than falling back to available records."""
    options[field] = period
    with pytest.raises(MarketDataSelectionError, match=expected):
        select_market_data(balances, prices, **options)


def test_missing_global_price_lists_only_commodities(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Report commodity price gaps without inventing per-region requirements."""
    with pytest.raises(MarketDataSelectionError) as caught:
        select_market_data(balances, prices[:1], **options)
    assert str(caught.value) == "Missing global prices for 2021-01-01: soybean_meal"


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("marketing_year", ""),
        ("marketing_year", "  "),
        ("marketing_year", None),
        ("marketing_year", 2020),
        ("price_month", None),
        ("price_month", "2021-01-01"),
        ("price_month", datetime(2021, 1, 1)),
        ("price_month", date(2021, 1, 2)),
        ("regions", []),
        ("commodities", []),
        ("regions", ["unknown"]),
        ("commodities", ["unknown"]),
        ("regions", None),
        ("commodities", None),
        ("regions", "brazil"),
        ("commodities", b"soybeans"),
    ],
)
def test_invalid_requests_are_selection_errors(
    balances: list[MarketBalance],
    prices: list[CommodityPrice],
    options: dict[str, Any],
    field: str,
    invalid: Any,
) -> None:
    """Validate period types, nonempty selections, and supported identifiers."""
    options[field] = invalid
    with pytest.raises(MarketDataSelectionError):
        select_market_data(balances, prices, **options)


def test_one_pass_iterables_and_repeated_identifiers(
    balances: list[MarketBalance], prices: list[CommodityPrice], options: dict[str, Any]
) -> None:
    """Consume generators once and collapse repeated identifiers into one request."""
    expected = select_market_data(balances, prices, **options)
    options.update(
        regions=(Region(region) for region in ("brazil", "brazil", "united_states")),
        commodities=(
            Commodity(item) for item in ("soybeans", "soybean_meal", "soybeans")
        ),
    )
    assert select_market_data(iter(balances), iter(prices), **options) == expected
