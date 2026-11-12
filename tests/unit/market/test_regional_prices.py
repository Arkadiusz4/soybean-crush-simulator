from collections.abc import Iterable
from datetime import date, datetime
from typing import cast

import pytest

from soybean_sim import market
from soybean_sim.agents import (
    AgentIdentity,
    AgentType,
    DecisionAction,
    Producer,
    ProducerConfig,
)
from soybean_sim.market import (
    MarketState,
    RegionalPrice,
    RegionalPriceError,
    _regional_prices,
    initialize_regional_prices,
    snapshot_for_region,
    validate_regional_prices,
)
from soybean_sim.models import Commodity, CommodityPrice, MarketSnapshot, Region


@pytest.fixture
def snapshot(state: MarketState) -> MarketSnapshot:
    """Provide distinct synthetic global prices and stocks from two regions."""
    return MarketSnapshot(
        month=state.snapshot.month,
        inventories=state.snapshot.inventories,
        prices=tuple(
            CommodityPrice(
                commodity=commodity,
                month=state.snapshot.month,
                price_usd_per_tonne=price,
                data_source="synthetic regional-view fixture",
            )
            for commodity, price in (
                (Commodity.SOYBEANS, 400),
                (Commodity.SOYBEAN_MEAL, 300),
                (Commodity.SOYBEAN_OIL, 800),
            )
        ),
    )


@pytest.fixture
def prices(snapshot: MarketSnapshot) -> tuple[RegionalPrice, ...]:
    """Provide independently constructed regional prices above and below baseline."""
    return tuple(
        RegionalPrice(
            month=snapshot.month,
            region=region,
            commodity=price.commodity,
            price_usd_per_tonne=price.price_usd_per_tonne * multiplier,
        )
        for region, multiplier in ((Region.BRAZIL, 0.8), (Region.UNITED_STATES, 1.2))
        for price in snapshot.prices
    )


def test_public_exports() -> None:
    """Expose regional initialization and observation adapters through market."""
    assert initialize_regional_prices is _regional_prices.initialize_regional_prices
    assert snapshot_for_region is _regional_prices.snapshot_for_region
    assert RegionalPriceError is _regional_prices.RegionalPriceError
    assert validate_regional_prices is _regional_prices.validate_regional_prices
    assert issubclass(RegionalPriceError, ValueError)
    assert {
        "initialize_regional_prices",
        "snapshot_for_region",
        "RegionalPriceError",
        "validate_regional_prices",
    } <= set(market.__all__)


@pytest.mark.parametrize("regions", [[Region.BRAZIL], list(Region)])
def test_initialization_copies_global_prices(
    snapshot: MarketSnapshot, regions: list[Region]
) -> None:
    """Initialize each requested region at the unchanged global commodity prices."""
    original = snapshot.model_dump_json()
    original_regions = regions.copy()
    result = initialize_regional_prices(snapshot, regions)
    expected = {
        (region, price.commodity): price.price_usd_per_tonne
        for region in regions
        for price in snapshot.prices
    }
    assert {
        (item.region, item.commodity): item.price_usd_per_tonne for item in result
    } == expected
    assert all(item.month == snapshot.month for item in result)
    assert [(item.region, item.commodity) for item in result] == sorted(expected)
    assert snapshot.model_dump_json() == original
    assert regions == original_regions


def test_initialization_order_and_generators(snapshot: MarketSnapshot) -> None:
    """Keep initialization stable under reversed price and region ordering."""
    expected = initialize_regional_prices(snapshot, list(Region))
    reordered = snapshot.model_copy(update={"prices": tuple(reversed(snapshot.prices))})
    assert initialize_regional_prices(reordered, reversed(list(Region))) == expected


@pytest.mark.parametrize(
    "regions",
    [
        [],
        [Region.BRAZIL, Region.BRAZIL],
        [Region.BRAZIL, "brazil"],
        ["unknown"],
        [None],
        "brazil",
        b"brazil",
        None,
    ],
)
def test_invalid_region_collections(snapshot: MarketSnapshot, regions: object) -> None:
    """Reject empty, duplicate, unsupported, and malformed region selections."""
    with pytest.raises(RegionalPriceError, match="region"):
        initialize_regional_prices(snapshot, cast(Iterable[Region], regions))


def test_known_string_region_identifiers(snapshot: MarketSnapshot) -> None:
    """Normalize supported serialized identifiers consistently with existing APIs."""
    assert initialize_regional_prices(
        snapshot, cast(Iterable[Region], ["brazil"])
    ) == initialize_regional_prices(snapshot, [Region.BRAZIL])


@pytest.mark.parametrize("commodity", list(Commodity))
def test_initialization_requires_all_global_commodities(
    snapshot: MarketSnapshot, commodity: Commodity
) -> None:
    """Reject a missing starting price rather than inventing its regional baseline."""
    partial = snapshot.model_copy(
        update={
            "prices": tuple(
                price for price in snapshot.prices if price.commodity != commodity
            )
        }
    )
    with pytest.raises(RegionalPriceError, match="Missing global commodity prices"):
        initialize_regional_prices(partial, [Region.BRAZIL])


@pytest.mark.parametrize("region", [Region.BRAZIL, Region.UNITED_STATES])
def test_regional_projection_preserves_month_inventories_and_inputs(
    snapshot: MarketSnapshot, prices: tuple[RegionalPrice, ...], region: Region
) -> None:
    """Project one region's prices while retaining all inventories and original data."""
    original_snapshot = snapshot.model_dump_json()
    original_prices = [item.model_dump_json() for item in prices]
    view = snapshot_for_region(snapshot, prices, region)
    expected = {
        item.commodity: item.price_usd_per_tonne
        for item in prices
        if item.region == region
    }
    assert {
        item.commodity: item.price_usd_per_tonne for item in view.prices
    } == expected
    assert all(
        item.month == snapshot.month and item.data_source == "simulated_regional"
        for item in view.prices
    )
    assert [item.commodity for item in view.prices] == sorted(expected)
    assert view.inventories == snapshot.inventories
    assert view.month == snapshot.month
    assert view is not snapshot
    assert snapshot.model_dump_json() == original_snapshot
    assert [item.model_dump_json() for item in prices] == original_prices


def test_regions_observe_different_prices(
    snapshot: MarketSnapshot, prices: tuple[RegionalPrice, ...]
) -> None:
    """Keep local simulated price observations distinct from global references."""
    brazil = snapshot_for_region(snapshot, prices, Region.BRAZIL)
    us = snapshot_for_region(snapshot, prices, Region.UNITED_STATES)
    assert [item.price_usd_per_tonne for item in brazil.prices] == [240, 640, 320]
    assert [item.price_usd_per_tonne for item in us.prices] == [360, 960, 480]
    assert brazil.prices != snapshot.prices != us.prices


def test_projection_order_and_generator_inputs(
    snapshot: MarketSnapshot, prices: tuple[RegionalPrice, ...]
) -> None:
    """Produce the same agent view regardless of record order or iterable reuse."""
    expected = snapshot_for_region(snapshot, prices, Region.BRAZIL)
    reordered = snapshot.model_copy(update={"prices": tuple(reversed(snapshot.prices))})
    assert snapshot_for_region(reordered, reversed(prices), Region.BRAZIL) == expected


def test_other_regions_need_not_have_complete_coverage(
    snapshot: MarketSnapshot, prices: tuple[RegionalPrice, ...]
) -> None:
    """Require coverage only for the requested region while validating other records."""
    subset = [price for price in prices if price.region == Region.BRAZIL]
    subset.append(prices[-1])
    assert snapshot_for_region(snapshot, subset, Region.BRAZIL) == snapshot_for_region(
        snapshot, prices, Region.BRAZIL
    )


@pytest.mark.parametrize("commodity", list(Commodity))
def test_projection_rejects_missing_regional_commodities(
    snapshot: MarketSnapshot, prices: tuple[RegionalPrice, ...], commodity: Commodity
) -> None:
    """Do not substitute global prices for missing requested regional observations."""
    subset = [
        price
        for price in prices
        if not (price.region == Region.BRAZIL and price.commodity == commodity)
    ]
    with pytest.raises(RegionalPriceError, match=r"Missing regional prices.*brazil"):
        snapshot_for_region(snapshot, subset, Region.BRAZIL)


def test_absent_requested_region_is_rejected(
    snapshot: MarketSnapshot, prices: tuple[RegionalPrice, ...]
) -> None:
    """Reject a requested region absent from the complete supplied price series."""
    with pytest.raises(
        RegionalPriceError, match="Missing regional prices for argentina"
    ):
        snapshot_for_region(snapshot, prices, Region.ARGENTINA)


def test_partial_global_snapshot_can_be_projected(
    snapshot: MarketSnapshot, prices: tuple[RegionalPrice, ...]
) -> None:
    """Match the view to the snapshot's existing commodity coverage."""
    partial = snapshot.model_copy(
        update={
            "prices": tuple(
                item for item in snapshot.prices if item.commodity == Commodity.SOYBEANS
            )
        }
    )
    subset = tuple(item for item in prices if item.commodity == Commodity.SOYBEANS)
    view = snapshot_for_region(partial, subset, Region.BRAZIL)
    assert len(view.prices) == 1
    assert view.prices[0].commodity is Commodity.SOYBEANS
    assert view.prices[0].price_usd_per_tonne == 320


@pytest.mark.parametrize("region", [Region.BRAZIL, Region.UNITED_STATES])
def test_unexpected_commodity_records_are_rejected(
    snapshot: MarketSnapshot, prices: tuple[RegionalPrice, ...], region: Region
) -> None:
    """Reject extra commodities even when they belong to an unrequested region."""
    partial = snapshot.model_copy(
        update={
            "prices": tuple(
                item for item in snapshot.prices if item.commodity == Commodity.SOYBEANS
            )
        }
    )
    supplied = [
        item
        for item in prices
        if item.commodity == Commodity.SOYBEANS
        or (item.region == region and item.commodity == Commodity.SOYBEAN_MEAL)
    ]
    with pytest.raises(RegionalPriceError, match="Unexpected regional commodity"):
        snapshot_for_region(partial, supplied, Region.BRAZIL)


@pytest.mark.parametrize("index", [0, 3])
@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_regional_prices_are_rejected(
    snapshot: MarketSnapshot,
    prices: tuple[RegionalPrice, ...],
    index: int,
    conflicting: bool,
) -> None:
    """Reject duplicates and conflicts across both selected and unselected regions."""
    duplicate = prices[index].model_copy(
        update={"price_usd_per_tonne": 999} if conflicting else {}
    )
    with pytest.raises(RegionalPriceError, match="Duplicate regional price"):
        snapshot_for_region(snapshot, (*prices, duplicate), Region.BRAZIL)


@pytest.mark.parametrize("index", [0, 3])
@pytest.mark.parametrize(
    "value", [0, -1, float("nan"), float("inf"), float("-inf"), True, "400", None]
)
def test_all_regional_price_values_are_revalidated(
    snapshot: MarketSnapshot,
    prices: tuple[RegionalPrice, ...],
    index: int,
    value: object,
) -> None:
    """Reject invalid copied prices before filtering for the requested region."""
    invalid = prices[index].model_copy(update={"price_usd_per_tonne": value})
    supplied = [
        invalid if position == index else item for position, item in enumerate(prices)
    ]
    with pytest.raises(RegionalPriceError, match="Invalid regional price"):
        snapshot_for_region(snapshot, supplied, Region.BRAZIL)


@pytest.mark.parametrize("index", [0, 3])
def test_mismatched_regional_months_are_rejected(
    snapshot: MarketSnapshot, prices: tuple[RegionalPrice, ...], index: int
) -> None:
    """Require snapshot timing for prices from all supplied regions."""
    invalid = prices[index].model_copy(update={"month": date(2021, 2, 1)})
    supplied = [
        invalid if position == index else item for position, item in enumerate(prices)
    ]
    with pytest.raises(
        RegionalPriceError, match=r"Regional price month.*snapshot month"
    ):
        snapshot_for_region(snapshot, supplied, Region.BRAZIL)


@pytest.mark.parametrize(
    "changes",
    [
        {"month": date(2021, 1, 2)},
        {"month": datetime(2021, 1, 1)},
        {"month": "2021-01-01"},
        {"region": "unknown"},
        {"commodity": "unknown"},
    ],
)
def test_invalid_regional_records_are_rejected(
    snapshot: MarketSnapshot,
    prices: tuple[RegionalPrice, ...],
    changes: dict[str, object],
) -> None:
    """Preserve date and identifier validation for unchecked regional model copies."""
    invalid = prices[0].model_copy(update=changes)
    with pytest.raises(RegionalPriceError, match="Invalid regional price"):
        snapshot_for_region(snapshot, [invalid, *prices[1:]], Region.BRAZIL)


@pytest.mark.parametrize("operation", ["initialize", "view"])
@pytest.mark.parametrize(
    "defect",
    [
        "month",
        "empty_prices",
        "duplicate_prices",
        "mismatched_price_month",
        "invalid_price",
        "duplicate_inventory",
        "invalid_stock",
    ],
)
def test_nested_snapshot_data_is_revalidated(
    snapshot: MarketSnapshot,
    prices: tuple[RegionalPrice, ...],
    operation: str,
    defect: str,
) -> None:
    """Reject invalid global observations before initialization or projection."""
    changes = {
        "month": {"month": date(2021, 1, 2)},
        "empty_prices": {"prices": ()},
        "duplicate_prices": {"prices": (*snapshot.prices, snapshot.prices[0])},
        "mismatched_price_month": {
            "prices": (
                snapshot.prices[0].model_copy(update={"month": date(2021, 2, 1)}),
                *snapshot.prices[1:],
            )
        },
        "invalid_price": {
            "prices": (
                snapshot.prices[0].model_copy(
                    update={"price_usd_per_tonne": float("inf")}
                ),
                *snapshot.prices[1:],
            )
        },
        "duplicate_inventory": {
            "inventories": (*snapshot.inventories, snapshot.inventories[0])
        },
        "invalid_stock": {
            "inventories": (
                snapshot.inventories[0].model_copy(update={"quantity_tonnes": -1}),
            )
        },
    }
    invalid = snapshot.model_copy(update=changes[defect])
    with pytest.raises(RegionalPriceError, match="Invalid market snapshot"):
        if operation == "initialize":
            initialize_regional_prices(invalid, [Region.BRAZIL])
        else:
            snapshot_for_region(invalid, prices, Region.BRAZIL)


@pytest.mark.parametrize("region", ["unknown", None, True, {}])
def test_invalid_projection_region(
    snapshot: MarketSnapshot, prices: tuple[RegionalPrice, ...], region: object
) -> None:
    """Require a supported region for the requested agent view."""
    with pytest.raises(RegionalPriceError, match="Invalid region"):
        snapshot_for_region(snapshot, prices, cast(Region, region))


def test_wrong_snapshot_types_are_domain_errors() -> None:
    """Reject arbitrary mappings in place of snapshots for either public function."""
    with pytest.raises(RegionalPriceError, match="snapshot must be a MarketSnapshot"):
        initialize_regional_prices(cast(MarketSnapshot, {}), [Region.BRAZIL])
    with pytest.raises(RegionalPriceError, match="snapshot must be a MarketSnapshot"):
        snapshot_for_region(cast(MarketSnapshot, {}), [], Region.BRAZIL)


def test_wrong_price_types_are_domain_errors(snapshot: MarketSnapshot) -> None:
    """Require model records within an iterable regional price collection."""
    with pytest.raises(RegionalPriceError, match="must contain RegionalPrice"):
        snapshot_for_region(snapshot, [cast(RegionalPrice, {})], Region.BRAZIL)
    with pytest.raises(RegionalPriceError, match="must be an iterable"):
        snapshot_for_region(
            snapshot, cast(Iterable[RegionalPrice], None), Region.BRAZIL
        )


def test_empty_regional_prices_do_not_fall_back(snapshot: MarketSnapshot) -> None:
    """Reject absent observations instead of silently using global references."""
    with pytest.raises(RegionalPriceError, match="Missing regional prices"):
        snapshot_for_region(snapshot, iter(()), Region.BRAZIL)


def test_existing_producer_uses_the_projected_price(
    snapshot: MarketSnapshot, prices: tuple[RegionalPrice, ...]
) -> None:
    """Change an existing producer's intention through its regional observation only."""
    producer = Producer(
        identity=AgentIdentity(
            agent_id="producer-br", agent_type=AgentType.PRODUCER, region=Region.BRAZIL
        ),
        config=ProducerConfig(reference_price_usd_per_tonne=400),
    )
    (global_decision,) = producer.decide(snapshot)
    regional_view = snapshot_for_region(snapshot, prices, Region.BRAZIL)
    (regional_decision,) = producer.decide(regional_view)
    assert global_decision.quantity_tonnes == 5000
    assert regional_decision.quantity_tonnes == 2000
    assert regional_decision.agent == producer.identity
    assert regional_decision.month == snapshot.month
    assert regional_decision.action is DecisionAction.OFFER_FOR_SALE
    assert regional_decision.commodity is Commodity.SOYBEANS
