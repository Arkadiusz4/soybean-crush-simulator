import math
from collections.abc import Mapping
from datetime import date, datetime
from itertools import permutations
from sys import float_info
from typing import cast

import pytest

from soybean_sim import market
from soybean_sim.market import (
    FlowSettlementError,
    MarketFlow,
    MarketFlowType,
    MarketState,
    MonthlyFlowConfig,
    _flow_settlement,
    generate_monthly_flows,
    settle_monthly_flows,
)
from soybean_sim.models import (
    Commodity,
    Inventory,
    MarketBalance,
    MarketSnapshot,
    Region,
)


def _stocks(state: MarketState) -> dict[tuple[Region, Commodity], float]:
    """Read regional stocks independently of inventory ordering."""
    return {
        (item.region, item.commodity): item.quantity_tonnes
        for item in state.snapshot.inventories
    }


def _with_stocks(
    state: MarketState, updates: Mapping[tuple[Region, Commodity], float]
) -> MarketState:
    """Replace synthetic stock values while retaining prices and month."""
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
def flow(state: MarketState) -> MarketFlow:
    """Provide synthetic soybean supply for Brazil in the state's month."""
    return MarketFlow(
        month=state.snapshot.month,
        region=Region.BRAZIL,
        commodity=Commodity.SOYBEANS,
        flow_type=MarketFlowType.SUPPLY,
        quantity_tonnes=3000,
    )


def test_public_exports() -> None:
    """Expose physical flow settlement and its error through the market package."""
    assert settle_monthly_flows is _flow_settlement.settle_monthly_flows
    assert FlowSettlementError is _flow_settlement.FlowSettlementError
    assert issubclass(FlowSettlementError, ValueError)
    assert {"settle_monthly_flows", "FlowSettlementError"} <= set(market.__all__)


@pytest.mark.parametrize(
    ("commodity", "flow_type", "region", "quantity", "change"),
    [
        (Commodity.SOYBEANS, MarketFlowType.SUPPLY, Region.BRAZIL, 3000, 3000),
        (
            Commodity.SOYBEAN_MEAL,
            MarketFlowType.CONSUMPTION,
            Region.UNITED_STATES,
            2,
            -2,
        ),
        (
            Commodity.SOYBEAN_OIL,
            MarketFlowType.CONSUMPTION,
            Region.UNITED_STATES,
            3,
            -3,
        ),
        (Commodity.SOYBEAN_MEAL, MarketFlowType.SUPPLY, Region.UNITED_STATES, 5000, 0),
        (Commodity.SOYBEAN_OIL, MarketFlowType.SUPPLY, Region.UNITED_STATES, 5000, 0),
    ],
)
def test_accounting_policy_preserves_other_stocks_prices_and_month(
    state: MarketState,
    flow: MarketFlow,
    commodity: Commodity,
    flow_type: MarketFlowType,
    region: Region,
    quantity: float,
    change: float,
) -> None:
    """Apply only soybean supply and product consumption as physical changes."""
    record = flow.model_copy(
        update={
            "commodity": commodity,
            "flow_type": flow_type,
            "region": region,
            "quantity_tonnes": quantity,
        }
    )
    original_state = state.model_dump_json()
    original_flow = record.model_dump_json()
    updated = settle_monthly_flows(state, [record])
    expected = _stocks(state)
    expected[region, commodity] += change
    assert _stocks(updated) == expected
    assert updated.snapshot.prices == state.snapshot.prices
    assert updated.snapshot.month == state.snapshot.month
    assert updated is not state
    assert state.model_dump_json() == original_state
    assert record.model_dump_json() == original_flow


@pytest.mark.parametrize("commodity", [Commodity.SOYBEAN_MEAL, Commodity.SOYBEAN_OIL])
def test_reference_supply_does_not_offset_consumption(
    state: MarketState, flow: MarketFlow, commodity: Commodity
) -> None:
    """Subtract product consumption even when reference production is enormous."""
    reference = flow.model_copy(
        update={
            "region": Region.UNITED_STATES,
            "commodity": commodity,
            "quantity_tonnes": float_info.max,
        }
    )
    consumption = reference.model_copy(
        update={"flow_type": MarketFlowType.CONSUMPTION, "quantity_tonnes": 2}
    )
    updated = settle_monthly_flows(state, [reference, consumption])
    assert (
        _stocks(updated)[Region.UNITED_STATES, commodity]
        == _stocks(state)[Region.UNITED_STATES, commodity] - 2
    )


@pytest.mark.parametrize("commodity", [Commodity.SOYBEAN_MEAL, Commodity.SOYBEAN_OIL])
def test_reference_supply_cannot_fund_consumption(
    state: MarketState, flow: MarketFlow, commodity: Commodity
) -> None:
    """Reject shortages without using USDA product production as physical stock."""
    reference = flow.model_copy(
        update={"commodity": commodity, "quantity_tonnes": 5000}
    )
    consumption = reference.model_copy(
        update={"flow_type": MarketFlowType.CONSUMPTION, "quantity_tonnes": 1}
    )
    original = state.model_dump_json()
    for ordered in permutations((reference, consumption)):
        with pytest.raises(FlowSettlementError, match="Insufficient inventory"):
            settle_monthly_flows(state, ordered)
    assert state.model_dump_json() == original


def test_multiple_regions_and_commodities_are_order_independent(
    state: MarketState, flow: MarketFlow
) -> None:
    """Settle a mixed batch deterministically without mutating inputs."""
    records = [flow]
    records.extend(
        flow.model_copy(
            update={
                "region": Region.UNITED_STATES,
                "commodity": commodity,
                "flow_type": flow_type,
                "quantity_tonnes": quantity,
            }
        )
        for commodity, flow_type, quantity in (
            (Commodity.SOYBEAN_MEAL, MarketFlowType.CONSUMPTION, 2),
            (Commodity.SOYBEAN_OIL, MarketFlowType.CONSUMPTION, 3),
            (Commodity.SOYBEAN_OIL, MarketFlowType.SUPPLY, 5000),
        )
    )
    expected = _stocks(state)
    expected[Region.BRAZIL, Commodity.SOYBEANS] += 3000
    expected[Region.UNITED_STATES, Commodity.SOYBEAN_MEAL] -= 2
    expected[Region.UNITED_STATES, Commodity.SOYBEAN_OIL] -= 3
    original_records = [item.model_dump_json() for item in records]
    original_state = state.model_dump_json()
    unordered = state.model_copy(
        update={
            "snapshot": state.snapshot.model_copy(
                update={"inventories": tuple(reversed(state.snapshot.inventories))}
            )
        }
    )
    result = settle_monthly_flows(state, records)
    assert _stocks(result) == expected
    for ordered in permutations(records):
        assert settle_monthly_flows(unordered, iter(ordered)) == result
    keys = [(item.region, item.commodity) for item in result.snapshot.inventories]
    assert keys == sorted(keys)
    assert [item.model_dump_json() for item in records] == original_records
    assert state.model_dump_json() == original_state


def test_generator_output_uses_the_explicit_accounting_policy(
    state: MarketState,
) -> None:
    """Keep generated meal/oil production as reference data while applying demand."""
    initial = _with_stocks(
        state,
        {
            (Region.BRAZIL, Commodity.SOYBEAN_MEAL): 10,
            (Region.BRAZIL, Commodity.SOYBEAN_OIL): 10,
        },
    )
    balances = [
        MarketBalance(
            region=Region.BRAZIL,
            commodity=commodity,
            marketing_year="2020",
            production_tonnes=1200,
            imports_tonnes=None,
            exports_tonnes=None,
            domestic_consumption_tonnes=60,
            beginning_stocks_tonnes=None,
            ending_stocks_tonnes=None,
        )
        for commodity in Commodity
    ]
    generated = generate_monthly_flows(
        balances, month=initial.snapshot.month, config=MonthlyFlowConfig()
    )
    assert len(generated) == 5
    result = settle_monthly_flows(initial, generated)
    assert _stocks(result)[Region.BRAZIL, Commodity.SOYBEANS] == 10100
    assert _stocks(result)[Region.BRAZIL, Commodity.SOYBEAN_MEAL] == 5
    assert _stocks(result)[Region.BRAZIL, Commodity.SOYBEAN_OIL] == 5


@pytest.mark.parametrize(
    ("commodity", "flow_type"),
    [
        (Commodity.SOYBEANS, MarketFlowType.SUPPLY),
        (Commodity.SOYBEAN_MEAL, MarketFlowType.CONSUMPTION),
        (Commodity.SOYBEAN_OIL, MarketFlowType.CONSUMPTION),
        (Commodity.SOYBEAN_MEAL, MarketFlowType.SUPPLY),
        (Commodity.SOYBEAN_OIL, MarketFlowType.SUPPLY),
    ],
)
def test_zero_flows_are_valid_noops(
    state: MarketState,
    flow: MarketFlow,
    commodity: Commodity,
    flow_type: MarketFlowType,
) -> None:
    """Accept zero physical and reference flows without changing any quantities."""
    record = flow.model_copy(
        update={"commodity": commodity, "flow_type": flow_type, "quantity_tonnes": 0}
    )
    assert settle_monthly_flows(state, [record]) == state


def test_empty_flows_return_a_new_equal_state(state: MarketState) -> None:
    """Validate the state and preserve its observations for an empty flow batch."""
    updated = settle_monthly_flows(state, iter(()))
    assert updated == state
    assert updated is not state


@pytest.mark.parametrize("quantity", [0, 1])
def test_soybean_consumption_is_unsupported(
    state: MarketState, flow: MarketFlow, quantity: float
) -> None:
    """Leave all soybean consumption to crushing, including zero-quantity records."""
    invalid = flow.model_copy(
        update={"flow_type": MarketFlowType.CONSUMPTION, "quantity_tonnes": quantity}
    )
    with pytest.raises(FlowSettlementError, match="Unsupported soybean consumption"):
        settle_monthly_flows(state, [invalid])


@pytest.mark.parametrize("quantity", [6, math.nextafter(5, math.inf)])
def test_insufficient_inventory_is_rejected_without_clamping(
    state: MarketState, flow: MarketFlow, quantity: float
) -> None:
    """Do not reduce consumption even when it exceeds stock by a tiny amount."""
    consumption = flow.model_copy(
        update={
            "region": Region.UNITED_STATES,
            "commodity": Commodity.SOYBEAN_MEAL,
            "flow_type": MarketFlowType.CONSUMPTION,
            "quantity_tonnes": quantity,
        }
    )
    original = state.model_dump_json()
    with pytest.raises(
        FlowSettlementError, match=r"Insufficient inventory.*united_states/soybean_meal"
    ):
        settle_monthly_flows(state, [flow, consumption])
    assert state.model_dump_json() == original


@pytest.mark.parametrize(
    ("commodity", "flow_type"),
    [
        (Commodity.SOYBEANS, MarketFlowType.SUPPLY),
        (Commodity.SOYBEAN_MEAL, MarketFlowType.CONSUMPTION),
        (Commodity.SOYBEAN_OIL, MarketFlowType.SUPPLY),
    ],
)
def test_missing_inventory_is_rejected_even_for_reference_supply(
    state: MarketState,
    flow: MarketFlow,
    commodity: Commodity,
    flow_type: MarketFlowType,
) -> None:
    """Require an explicit stock record for every referenced regional commodity."""
    record = flow.model_copy(update={"commodity": commodity, "flow_type": flow_type})
    initial = MarketState.from_snapshot(
        MarketSnapshot(
            month=state.snapshot.month,
            prices=state.snapshot.prices,
            inventories=tuple(
                item
                for item in state.snapshot.inventories
                if (item.region, item.commodity) != (Region.BRAZIL, commodity)
            ),
        )
    )
    with pytest.raises(FlowSettlementError, match="Missing inventory for brazil"):
        settle_monthly_flows(initial, [record])


@pytest.mark.parametrize(
    ("commodity", "flow_type"),
    [
        (Commodity.SOYBEANS, MarketFlowType.SUPPLY),
        (Commodity.SOYBEAN_MEAL, MarketFlowType.CONSUMPTION),
        (Commodity.SOYBEAN_OIL, MarketFlowType.SUPPLY),
    ],
)
@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_keys_are_rejected_including_reference_flows(
    state: MarketState,
    flow: MarketFlow,
    commodity: Commodity,
    flow_type: MarketFlowType,
    conflicting: bool,
) -> None:
    """Reject identical duplicates and different quantities under one flow key."""
    record = flow.model_copy(update={"commodity": commodity, "flow_type": flow_type})
    duplicate = record.model_copy(update={"quantity_tonnes": 10} if conflicting else {})
    with pytest.raises(FlowSettlementError, match="Duplicate flow"):
        settle_monthly_flows(state, [record, duplicate])


def test_flow_month_must_match_state(state: MarketState, flow: MarketFlow) -> None:
    """Reject a valid monthly flow belonging to a different simulation step."""
    other_month = flow.model_copy(update={"month": date(2021, 2, 1)})
    with pytest.raises(FlowSettlementError, match=r"Flow month.*market month"):
        settle_monthly_flows(state, [other_month])


@pytest.mark.parametrize("commodity", [Commodity.SOYBEANS, Commodity.SOYBEAN_MEAL])
@pytest.mark.parametrize(
    "quantity", [-1, float("nan"), float("inf"), float("-inf"), True, "10", None]
)
def test_quantities_are_revalidated_before_ignoring_reference_supply(
    state: MarketState, flow: MarketFlow, commodity: Commodity, quantity: object
) -> None:
    """Reject invalid quantities in both physical supply and ignored product supply."""
    record = flow.model_copy(
        update={"commodity": commodity, "quantity_tonnes": quantity}
    )
    with pytest.raises(FlowSettlementError, match="Invalid flow"):
        settle_monthly_flows(state, [record])


@pytest.mark.parametrize(
    "changes",
    [
        {"month": date(2021, 1, 2)},
        {"month": datetime(2021, 1, 1)},
        {"month": "2021-01-01"},
        {"flow_type": "unknown"},
        {"commodity": "unknown"},
        {"region": "unknown"},
    ],
)
def test_invalid_flow_contracts_are_rejected(
    state: MarketState, flow: MarketFlow, changes: dict[str, object]
) -> None:
    """Preserve strict calendar, flow direction, region, and commodity validation."""
    with pytest.raises(FlowSettlementError, match="Invalid flow"):
        settle_monthly_flows(state, [flow.model_copy(update=changes)])


@pytest.mark.parametrize("defect", ["month", "duplicate_stock", "stock", "price"])
def test_invalid_state_is_rejected_before_empty_batch(
    state: MarketState, defect: str
) -> None:
    """Revalidate nested state data even when no flow records need settlement."""
    changes = {
        "month": {"month": date(2021, 1, 2)},
        "duplicate_stock": {
            "inventories": (*state.snapshot.inventories, state.snapshot.inventories[0])
        },
        "stock": {
            "inventories": (
                state.snapshot.inventories[0].model_copy(
                    update={"quantity_tonnes": float("nan")}
                ),
            )
        },
        "price": {
            "prices": (
                state.snapshot.prices[0].model_copy(update={"price_usd_per_tonne": -1}),
            )
        },
    }
    invalid = state.model_copy(
        update={"snapshot": state.snapshot.model_copy(update=changes[defect])}
    )
    with pytest.raises(FlowSettlementError, match="Invalid market state"):
        settle_monthly_flows(invalid, [])


@pytest.mark.parametrize("kind", ["state", "flow"])
def test_wrong_record_types_are_domain_errors(state: MarketState, kind: str) -> None:
    """Require typed models rather than silently coercing dictionaries."""
    if kind == "state":
        with pytest.raises(FlowSettlementError, match="state must be a MarketState"):
            settle_monthly_flows(cast(MarketState, {}), [])
    else:
        with pytest.raises(FlowSettlementError, match="flows must contain"):
            settle_monthly_flows(state, [cast(MarketFlow, {})])


def test_supply_overflow_is_rejected(state: MarketState, flow: MarketFlow) -> None:
    """Report finite inputs whose combined physical stock exceeds float capacity."""
    initial = _with_stocks(state, {(Region.BRAZIL, Commodity.SOYBEANS): float_info.max})
    record = flow.model_copy(update={"quantity_tonnes": float_info.max})
    original = initial.model_dump_json()
    with pytest.raises(FlowSettlementError, match="Inventory overflow"):
        settle_monthly_flows(initial, [record])
    assert initial.model_dump_json() == original


@pytest.mark.parametrize("quantity", [0, 0.1, 0.3])
def test_fractional_and_exact_consumption_stay_nonnegative(
    state: MarketState, flow: MarketFlow, quantity: float
) -> None:
    """Keep fractional tonnes unrounded and allow exact exhaustion of product stock."""
    initial = _with_stocks(state, {(Region.BRAZIL, Commodity.SOYBEAN_OIL): 0.3})
    record = flow.model_copy(
        update={
            "commodity": Commodity.SOYBEAN_OIL,
            "flow_type": MarketFlowType.CONSUMPTION,
            "quantity_tonnes": quantity,
        }
    )
    result = settle_monthly_flows(initial, [record])
    ending = _stocks(result)[Region.BRAZIL, Commodity.SOYBEAN_OIL]
    assert ending == pytest.approx(0.3 - quantity)
    assert ending >= 0
