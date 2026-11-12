import math
from datetime import date, datetime
from itertools import permutations, product
from sys import float_info
from typing import Any

import pytest

from soybean_sim import market
from soybean_sim.market import (
    MarketFlow,
    MarketFlowType,
    MarketPressure,
    MarketPressureError,
    PriceDynamicsConfig,
    RegionalPrice,
    TradeExecution,
    calculate_market_pressures,
    update_regional_prices,
)
from soybean_sim.models import Commodity, Region


@pytest.fixture
def activity() -> dict[str, Any]:
    """Provide empty synthetic activity across all supported regions.

    Returns:
        dict[str, Any]: Public API inputs, including mutable test payloads.
    """
    return {
        "month": date(2025, 1, 1),
        "regions": list(Region),
        "flows": [],
        "trades": [],
        "crushed_soybeans_tonnes": {},
        "crushing_outputs_tonnes": {},
    }


@pytest.fixture
def flow() -> MarketFlow:
    """Provide synthetic Brazilian soybean production for January.

    Returns:
        MarketFlow: One hundred tonnes of soybean supply.
    """
    return MarketFlow(
        month=date(2025, 1, 1),
        region=Region.BRAZIL,
        commodity=Commodity.SOYBEANS,
        flow_type=MarketFlowType.SUPPLY,
        quantity_tonnes=100,
    )


@pytest.fixture
def trade() -> TradeExecution:
    """Provide a synthetic Brazilian soybean export to the EU.

    Returns:
        TradeExecution: Twenty executed tonnes, with no inventory assumptions.
    """
    return TradeExecution(
        month=date(2025, 1, 1),
        seller_agent_id="seller-br",
        buyer_agent_id="buyer-eu",
        source_region=Region.BRAZIL,
        destination_region=Region.EUROPEAN_UNION,
        commodity=Commodity.SOYBEANS,
        quantity_tonnes=20,
    )


def _signals(
    records: tuple[MarketPressure, ...],
) -> dict[tuple[Region, Commodity], tuple[float, float]]:
    """Read supply/demand pairs independently of record ordering.

    Args:
        records (tuple[MarketPressure, ...]): Calculated market pressures.

    Returns:
        dict[tuple[Region, Commodity], tuple[float, float]]: Supply and demand tonnes.
    """
    return {
        (item.region, item.commodity): (item.supply_tonnes, item.demand_tonnes)
        for item in records
    }


def test_public_exports() -> None:
    """Check the package-level pressure API.

    Returns:
        None: Confirms the function and error are public exports.
    """
    assert callable(calculate_market_pressures)
    assert issubclass(MarketPressureError, ValueError)
    assert {"calculate_market_pressures", "MarketPressureError"} <= set(market.__all__)


def test_zero_activity_covers_all_requested_markets(activity: dict[str, Any]) -> None:
    """Keep inactive regional commodities explicitly present.

    Args:
        activity (dict[str, Any]): Empty activity inputs.

    Returns:
        None: Confirms sorted zero signals and matching months.
    """
    result = calculate_market_pressures(**activity)
    expected = {
        (region, commodity): (0, 0) for region, commodity in product(Region, Commodity)
    }
    assert isinstance(result, tuple)
    assert _signals(result) == expected
    assert [(item.region, item.commodity) for item in result] == sorted(expected)
    assert all(item.month == activity["month"] for item in result)


@pytest.mark.parametrize(
    ("commodity", "flow_type", "expected"),
    [
        (Commodity.SOYBEANS, MarketFlowType.SUPPLY, (100, 0)),
        (Commodity.SOYBEAN_MEAL, MarketFlowType.CONSUMPTION, (0, 100)),
        (Commodity.SOYBEAN_OIL, MarketFlowType.CONSUMPTION, (0, 100)),
        (Commodity.SOYBEAN_MEAL, MarketFlowType.SUPPLY, (0, 0)),
        (Commodity.SOYBEAN_OIL, MarketFlowType.SUPPLY, (0, 0)),
    ],
)
def test_physical_flow_policy(
    activity: dict[str, Any],
    flow: MarketFlow,
    commodity: Commodity,
    flow_type: MarketFlowType,
    expected: tuple[float, float],
) -> None:
    """Apply the same flow accounting as physical settlement.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        flow (MarketFlow): Synthetic supply record.
        commodity (Commodity): Tested commodity.
        flow_type (MarketFlowType): Tested flow direction.
        expected (tuple[float, float]): Expected supply/demand pair.

    Returns:
        None: Confirms physical contributions and excluded reference supply.
    """
    activity["flows"] = [
        flow.model_copy(update={"commodity": commodity, "flow_type": flow_type})
    ]
    result = _signals(calculate_market_pressures(**activity))
    assert result.pop((Region.BRAZIL, commodity)) == expected
    assert all(value == (0, 0) for value in result.values())


def test_crushing_uses_actual_inputs_and_outputs(activity: dict[str, Any]) -> None:
    """Preserve supplied crushing amounts without assuming an 80/20 split.

    Args:
        activity (dict[str, Any]): Empty activity inputs.

    Returns:
        None: Confirms input demand and independently reported product supply.
    """
    activity["crushed_soybeans_tonnes"] = {Region.BRAZIL: 1000}
    activity["crushing_outputs_tonnes"] = {
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL): 725,
        (Region.BRAZIL, Commodity.SOYBEAN_OIL): 175,
    }
    signals = _signals(calculate_market_pressures(**activity))
    assert signals[Region.BRAZIL, Commodity.SOYBEANS] == (0, 1000)
    assert signals[Region.BRAZIL, Commodity.SOYBEAN_MEAL] == (725, 0)
    assert signals[Region.BRAZIL, Commodity.SOYBEAN_OIL] == (175, 0)


def test_missing_optional_outputs_are_not_inferred(activity: dict[str, Any]) -> None:
    """Keep absent output observations at zero instead of inventing yields.

    Args:
        activity (dict[str, Any]): Empty activity inputs.

    Returns:
        None: Confirms omitted records add no product supply.
    """
    activity["regions"] = [Region.BRAZIL]
    activity["crushed_soybeans_tonnes"] = {Region.BRAZIL: 100}
    assert _signals(calculate_market_pressures(**activity)) == {
        (Region.BRAZIL, Commodity.SOYBEANS): (0, 100),
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL): (0, 0),
        (Region.BRAZIL, Commodity.SOYBEAN_OIL): (0, 0),
    }


@pytest.mark.parametrize("commodity", list(Commodity))
def test_trade_is_export_demand_and_import_supply(
    activity: dict[str, Any], trade: TradeExecution, commodity: Commodity
) -> None:
    """Count only physical executed quantities on both sides of each trade.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        trade (TradeExecution): Synthetic executed trade.
        commodity (Commodity): Commodity transferred.

    Returns:
        None: Confirms source demand and destination supply.
    """
    activity["trades"] = [trade.model_copy(update={"commodity": commodity})]
    result = _signals(calculate_market_pressures(**activity))
    assert result.pop((Region.BRAZIL, commodity)) == (0, 20)
    assert result.pop((Region.EUROPEAN_UNION, commodity)) == (20, 0)
    assert all(value == (0, 0) for value in result.values())


def test_multiple_trades_accumulate(
    activity: dict[str, Any], trade: TradeExecution
) -> None:
    """Sum separate physical executions for the same destination commodity.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        trade (TradeExecution): Synthetic Brazilian export.

    Returns:
        None: Confirms multiple sources contribute to one regional supply.
    """
    activity["trades"] = [
        trade,
        trade.model_copy(
            update={
                "seller_agent_id": "seller-ar",
                "source_region": Region.ARGENTINA,
                "quantity_tonnes": 30,
            }
        ),
    ]
    signals = _signals(calculate_market_pressures(**activity))
    assert signals[Region.EUROPEAN_UNION, Commodity.SOYBEANS] == (50, 0)
    assert signals[Region.BRAZIL, Commodity.SOYBEANS] == (0, 20)
    assert signals[Region.ARGENTINA, Commodity.SOYBEANS] == (0, 30)


def test_combined_activity_is_deterministic_and_immutable(
    activity: dict[str, Any], flow: MarketFlow, trade: TradeExecution
) -> None:
    """Combine all sources without double-counting or changing input records.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        flow (MarketFlow): Soybean supply record.
        trade (TradeExecution): Soybean export record.

    Returns:
        None: Confirms accounting, ordering independence, and input immutability.
    """
    flows = [
        flow,
        flow.model_copy(
            update={
                "commodity": Commodity.SOYBEAN_MEAL,
                "flow_type": MarketFlowType.CONSUMPTION,
                "quantity_tonnes": 10,
            }
        ),
        flow.model_copy(
            update={"commodity": Commodity.SOYBEAN_OIL, "quantity_tonnes": 5000}
        ),
    ]
    trades = [
        trade,
        trade.model_copy(
            update={
                "seller_agent_id": "seller-eu",
                "buyer_agent_id": "buyer-br",
                "source_region": Region.EUROPEAN_UNION,
                "destination_region": Region.BRAZIL,
                "commodity": Commodity.SOYBEAN_MEAL,
                "quantity_tonnes": 5,
            }
        ),
        trade.model_copy(
            update={
                "buyer_agent_id": "buyer-us",
                "destination_region": Region.UNITED_STATES,
                "commodity": Commodity.SOYBEAN_OIL,
                "quantity_tonnes": 3,
            }
        ),
    ]
    crushed = {Region.BRAZIL: 40}
    outputs = {
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL): 27,
        (Region.BRAZIL, Commodity.SOYBEAN_OIL): 9,
    }
    activity.update(
        flows=flows,
        trades=trades,
        crushed_soybeans_tonnes=crushed,
        crushing_outputs_tonnes=outputs,
    )
    original_flows = [item.model_dump_json() for item in flows]
    original_trades = [item.model_dump_json() for item in trades]
    result = calculate_market_pressures(**activity)
    expected = {
        (region, commodity): (0, 0) for region, commodity in product(Region, Commodity)
    }
    expected.update(
        {
            (Region.BRAZIL, Commodity.SOYBEANS): (100, 60),
            (Region.EUROPEAN_UNION, Commodity.SOYBEANS): (20, 0),
            (Region.BRAZIL, Commodity.SOYBEAN_MEAL): (32, 10),
            (Region.EUROPEAN_UNION, Commodity.SOYBEAN_MEAL): (0, 5),
            (Region.BRAZIL, Commodity.SOYBEAN_OIL): (9, 3),
            (Region.UNITED_STATES, Commodity.SOYBEAN_OIL): (3, 0),
        }
    )
    assert _signals(result) == expected
    for ordered_flows in permutations(flows):
        for ordered_trades in permutations(trades):
            reordered = dict(
                activity,
                regions=reversed(list(Region)),
                flows=iter(ordered_flows),
                trades=iter(ordered_trades),
                crushing_outputs_tonnes=dict(reversed(list(outputs.items()))),
            )
            assert calculate_market_pressures(**reordered) == result
    assert [item.model_dump_json() for item in flows] == original_flows
    assert [item.model_dump_json() for item in trades] == original_trades
    assert crushed == {Region.BRAZIL: 40}
    assert outputs == {
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL): 27,
        (Region.BRAZIL, Commodity.SOYBEAN_OIL): 9,
    }


def test_pressures_drive_regional_price_updates(
    activity: dict[str, Any], flow: MarketFlow
) -> None:
    """Feed physical pressure directly into the existing pricing component.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        flow (MarketFlow): One hundred tonnes of soybean production.

    Returns:
        None: Confirms a regional demand surplus increases next-month prices.
    """
    activity.update(flows=[flow], crushed_soybeans_tonnes={Region.BRAZIL: 140})
    pressures = calculate_market_pressures(**activity)
    prices = [
        RegionalPrice(
            month=activity["month"],
            region=item.region,
            commodity=item.commodity,
            price_usd_per_tonne=400,
        )
        for item in pressures
    ]
    updated = update_regional_prices(prices, pressures, config=PriceDynamicsConfig())
    assert all(item.month == date(2025, 2, 1) for item in updated)
    for price in updated:
        expected = (
            432
            if (price.region, price.commodity) == (Region.BRAZIL, Commodity.SOYBEANS)
            else 400
        )
        assert price.price_usd_per_tonne == pytest.approx(expected)


@pytest.mark.parametrize("quantity", [0, 1000])
def test_reported_outputs_can_match_or_fall_below_input(
    activity: dict[str, Any], quantity: float
) -> None:
    """Accept zero activity and incomplete mass recovery without inventing by-products.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        quantity (float): Executed soybean input tonnes.

    Returns:
        None: Confirms valid input/output accounting.
    """
    activity["crushed_soybeans_tonnes"] = {Region.BRAZIL: quantity}
    activity["crushing_outputs_tonnes"] = {
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL): quantity * 0.8,
        (Region.BRAZIL, Commodity.SOYBEAN_OIL): quantity * 0.2,
    }
    signals = _signals(calculate_market_pressures(**activity))
    assert signals[Region.BRAZIL, Commodity.SOYBEANS] == (0, quantity)


@pytest.mark.parametrize(
    ("input_tonnes", "excess", "accepted"),
    [
        (1000, 5e-10, True),
        (1000, 1e-5, False),
        (1e9, 5e-4, True),
        (1e9, 0.01, False),
        (0, 1, False),
    ],
)
def test_crushing_output_tolerance(
    activity: dict[str, Any], input_tonnes: float, excess: float, accepted: bool
) -> None:
    """Allow only rounding-scale output excess, including for large inputs.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        input_tonnes (float): Actual soybean input.
        excess (float): Output amount above input.
        accepted (bool): Whether the documented tolerance permits this excess.

    Returns:
        None: Confirms mass checks use both relative and absolute tolerance.
    """
    activity["crushed_soybeans_tonnes"] = {Region.BRAZIL: input_tonnes}
    activity["crushing_outputs_tonnes"] = {
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL): input_tonnes + excess
    }
    if accepted:
        assert _signals(calculate_market_pressures(**activity))[
            Region.BRAZIL, Commodity.SOYBEAN_MEAL
        ] == (input_tonnes + excess, 0)
    else:
        with pytest.raises(
            MarketPressureError, match="Crushing outputs exceed soybean input"
        ):
            calculate_market_pressures(**activity)


@pytest.mark.parametrize(
    "month", [date(2025, 1, 2), datetime(2025, 1, 1), "2025-01-01", None, 0]
)
def test_invalid_month(activity: dict[str, Any], month: object) -> None:
    """Require an actual first-of-month date.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        month (object): Invalid candidate month.

    Returns:
        None: Confirms date validation uses the pressure domain error.
    """
    activity["month"] = month
    with pytest.raises(MarketPressureError, match="first day of a month"):
        calculate_market_pressures(**activity)


@pytest.mark.parametrize(
    "regions",
    [
        [],
        [Region.BRAZIL, Region.BRAZIL],
        [Region.BRAZIL, "brazil"],
        ["unknown"],
        "brazil",
        b"brazil",
        None,
    ],
)
def test_invalid_requested_regions(activity: dict[str, Any], regions: object) -> None:
    """Reject empty, duplicated, or malformed selections.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        regions (object): Invalid region selection.

    Returns:
        None: Confirms requested-region validation.
    """
    activity["regions"] = regions
    with pytest.raises(MarketPressureError, match="region"):
        calculate_market_pressures(**activity)


@pytest.mark.parametrize(
    "source", ["reference_flow", "trade_source", "trade_destination", "input", "output"]
)
def test_activity_outside_requested_regions_is_rejected(
    activity: dict[str, Any], flow: MarketFlow, trade: TradeExecution, source: str
) -> None:
    """Validate region coverage even for ignored reference supply and zero crushing.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        flow (MarketFlow): Synthetic soybean supply.
        trade (TradeExecution): Synthetic trade.
        source (str): Activity location to invalidate.

    Returns:
        None: Confirms unrequested activity is never silently ignored.
    """
    activity["regions"] = [Region.BRAZIL]
    if source == "reference_flow":
        activity["flows"] = [
            flow.model_copy(
                update={
                    "region": Region.UNITED_STATES,
                    "commodity": Commodity.SOYBEAN_MEAL,
                }
            )
        ]
    elif source == "trade_source":
        activity["trades"] = [
            trade.model_copy(
                update={
                    "source_region": Region.ARGENTINA,
                    "destination_region": Region.BRAZIL,
                }
            )
        ]
    elif source == "trade_destination":
        activity["trades"] = [trade]
    elif source == "input":
        activity["crushed_soybeans_tonnes"] = {Region.UNITED_STATES: 0}
    else:
        activity["crushing_outputs_tonnes"] = {
            (Region.UNITED_STATES, Commodity.SOYBEAN_MEAL): 0
        }
    with pytest.raises(MarketPressureError, match="outside requested regions"):
        calculate_market_pressures(**activity)


@pytest.mark.parametrize("reference", [False, True])
@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_flow_keys(
    activity: dict[str, Any], flow: MarketFlow, reference: bool, conflicting: bool
) -> None:
    """Reject duplicate physical and reference flow keys.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        flow (MarketFlow): Synthetic supply record.
        reference (bool): Whether to use excluded product supply.
        conflicting (bool): Whether repeated quantities differ.

    Returns:
        None: Confirms duplicates are invalid regardless of their quantities.
    """
    first = (
        flow.model_copy(update={"commodity": Commodity.SOYBEAN_MEAL})
        if reference
        else flow
    )
    second = first.model_copy(update={"quantity_tonnes": 101} if conflicting else {})
    activity["flows"] = [first, second]
    with pytest.raises(MarketPressureError, match="Duplicate flow"):
        calculate_market_pressures(**activity)


def test_duplicate_trades(activity: dict[str, Any], trade: TradeExecution) -> None:
    """Reject identical executed trades after record normalization.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        trade (TradeExecution): Synthetic execution.

    Returns:
        None: Confirms repeated executions cannot double-count trade pressure.
    """
    activity["trades"] = [
        trade,
        trade.model_copy(update={"seller_agent_id": " seller-br "}),
    ]
    with pytest.raises(MarketPressureError, match="Duplicate trade"):
        calculate_market_pressures(**activity)


@pytest.mark.parametrize("source", ["flows", "trades"])
def test_activity_months_must_match(
    activity: dict[str, Any], flow: MarketFlow, trade: TradeExecution, source: str
) -> None:
    """Reject activity from another valid calendar month.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        flow (MarketFlow): Synthetic supply record.
        trade (TradeExecution): Synthetic execution.
        source (str): Record collection to invalidate.

    Returns:
        None: Confirms all records match the requested month.
    """
    record = flow if source == "flows" else trade
    activity[source] = [record.model_copy(update={"month": date(2025, 2, 1)})]
    with pytest.raises(MarketPressureError, match=r"month.*does not match"):
        calculate_market_pressures(**activity)


@pytest.mark.parametrize("source", ["flows", "trades"])
@pytest.mark.parametrize(
    "changes",
    [
        {"month": "2025-01-01"},
        {"region": "unknown"},
        {"commodity": "unknown"},
        {"quantity_tonnes": float("nan")},
    ],
)
def test_nested_activity_is_revalidated(
    activity: dict[str, Any],
    flow: MarketFlow,
    trade: TradeExecution,
    source: str,
    changes: dict[str, object],
) -> None:
    """Preserve strict model validation for unchecked activity copies.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        flow (MarketFlow): Synthetic supply record.
        trade (TradeExecution): Synthetic execution.
        source (str): Record collection to invalidate.
        changes (dict[str, object]): Invalid record fields.

    Returns:
        None: Confirms invalid records use the pressure error contract.
    """
    record = flow if source == "flows" else trade
    if source == "trades" and "region" in changes:
        changes = {"source_region": "unknown"}
    activity[source] = [record.model_copy(update=changes)]
    with pytest.raises(MarketPressureError, match="Invalid"):
        calculate_market_pressures(**activity)


def test_soybean_consumption_is_unsupported(
    activity: dict[str, Any], flow: MarketFlow
) -> None:
    """Keep soybean use restricted to executed crushing and exports.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        flow (MarketFlow): Synthetic soybean supply record.

    Returns:
        None: Confirms unsupported consumption is rejected.
    """
    activity["flows"] = [
        flow.model_copy(update={"flow_type": MarketFlowType.CONSUMPTION})
    ]
    with pytest.raises(MarketPressureError, match="Unsupported soybean consumption"):
        calculate_market_pressures(**activity)


@pytest.mark.parametrize(
    "source", ["crushed_soybeans_tonnes", "crushing_outputs_tonnes"]
)
@pytest.mark.parametrize(
    "quantity",
    [-1, float("nan"), float("inf"), float("-inf"), True, "1", None, 10**1000],
)
def test_invalid_crushing_quantities(
    activity: dict[str, Any], source: str, quantity: object
) -> None:
    """Use the existing finite nonnegative tonne validation for actual crushing.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        source (str): Crushing mapping to invalidate.
        quantity (object): Invalid tonne value.

    Returns:
        None: Confirms negative, nonfinite, and coerced quantities are rejected.
    """
    key = (
        Region.BRAZIL
        if source == "crushed_soybeans_tonnes"
        else (Region.BRAZIL, Commodity.SOYBEAN_MEAL)
    )
    activity[source] = {key: quantity}
    with pytest.raises(MarketPressureError, match="Invalid quantity"):
        calculate_market_pressures(**activity)


@pytest.mark.parametrize(
    "key",
    [
        "brazil",
        (Region.BRAZIL,),
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL, "extra"),
        (Region.BRAZIL, "unknown"),
        (Region.BRAZIL, Commodity.SOYBEANS),
        ("unknown", Commodity.SOYBEAN_MEAL),
    ],
)
def test_invalid_crushing_output_keys(activity: dict[str, Any], key: object) -> None:
    """Require requested regions and meal/oil commodity keys for output records.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        key (object): Invalid output mapping key.

    Returns:
        None: Confirms malformed or unsupported keys are rejected even at zero.
    """
    activity["crushing_outputs_tonnes"] = {key: 0}
    with pytest.raises(MarketPressureError):
        calculate_market_pressures(**activity)


def test_invalid_crushing_input_region(activity: dict[str, Any]) -> None:
    """Validate region identifiers in the soybean input mapping.

    Args:
        activity (dict[str, Any]): Empty activity inputs.

    Returns:
        None: Confirms invalid mapping regions are rejected.
    """
    activity["crushed_soybeans_tonnes"] = {"unknown": 0}
    with pytest.raises(MarketPressureError, match="Invalid activity region"):
        calculate_market_pressures(**activity)


@pytest.mark.parametrize(
    "source", ["flows", "trades", "crushed_soybeans_tonnes", "crushing_outputs_tonnes"]
)
def test_invalid_collections(activity: dict[str, Any], source: str) -> None:
    """Reject missing collections rather than interpreting None as no activity.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        source (str): Collection to invalidate.

    Returns:
        None: Confirms collection-type errors use the domain exception.
    """
    activity[source] = None
    with pytest.raises(MarketPressureError):
        calculate_market_pressures(**activity)


@pytest.mark.parametrize("source", ["flows", "trades"])
def test_wrong_activity_record_types(activity: dict[str, Any], source: str) -> None:
    """Require typed flow and execution records.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        source (str): Activity collection to invalidate.

    Returns:
        None: Confirms arbitrary dictionaries are rejected.
    """
    activity[source] = [{}]
    with pytest.raises(MarketPressureError, match="Expected"):
        calculate_market_pressures(**activity)


@pytest.mark.parametrize("side", ["supply", "demand", "crushing_outputs"])
def test_overflow_is_rejected(
    activity: dict[str, Any], flow: MarketFlow, trade: TradeExecution, side: str
) -> None:
    """Reject totals that exceed finite floating-point capacity.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        flow (MarketFlow): Synthetic soybean production.
        trade (TradeExecution): Synthetic execution.
        side (str): Contribution sum to overflow.

    Returns:
        None: Confirms totals fail before producing pressure records.
    """
    if side == "supply":
        activity["flows"] = [
            flow.model_copy(update={"quantity_tonnes": float_info.max})
        ]
        activity["trades"] = [
            trade.model_copy(
                update={
                    "source_region": Region.EUROPEAN_UNION,
                    "destination_region": Region.BRAZIL,
                    "quantity_tonnes": float_info.max,
                }
            )
        ]
    elif side == "demand":
        activity["crushed_soybeans_tonnes"] = {Region.BRAZIL: float_info.max}
        activity["trades"] = [
            trade.model_copy(update={"quantity_tonnes": float_info.max})
        ]
    else:
        activity["crushed_soybeans_tonnes"] = {Region.BRAZIL: float_info.max}
        activity["crushing_outputs_tonnes"] = {
            (Region.BRAZIL, Commodity.SOYBEAN_MEAL): float_info.max,
            (Region.BRAZIL, Commodity.SOYBEAN_OIL): float_info.max,
        }
    with pytest.raises(MarketPressureError, match="Quantity overflow"):
        calculate_market_pressures(**activity)


def test_stable_trade_summation(
    activity: dict[str, Any], trade: TradeExecution
) -> None:
    """Preserve small executed contributions beside much larger ones.

    Args:
        activity (dict[str, Any]): Empty activity inputs.
        trade (TradeExecution): Synthetic soybean export.

    Returns:
        None: Confirms summation remains stable under every trade ordering.
    """
    trades = [
        trade.model_copy(
            update={"buyer_agent_id": f"buyer-{index}", "quantity_tonnes": quantity}
        )
        for index, quantity in enumerate((1e16, 1, 1, 1))
    ]
    expected = math.fsum(item.quantity_tonnes for item in trades)
    for ordered in permutations(trades):
        result = _signals(calculate_market_pressures(**dict(activity, trades=ordered)))
        assert result[Region.BRAZIL, Commodity.SOYBEANS] == (0, expected)
        assert result[Region.EUROPEAN_UNION, Commodity.SOYBEANS] == (expected, 0)
