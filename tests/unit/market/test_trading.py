from datetime import date, datetime
from itertools import permutations
from typing import cast

import pytest

from soybean_sim import market
from soybean_sim.agents import AgentDecision, AgentIdentity, AgentType, DecisionAction
from soybean_sim.market import (
    TradeExecution,
    TradeMatchingError,
    match_interregional_trades,
    trading,
)
from soybean_sim.models import Commodity, Inventory, Region


def _decision(
    agent_id: str,
    region: Region,
    action: DecisionAction,
    quantity: float,
    commodity: Commodity = Commodity.SOYBEANS,
) -> AgentDecision:
    """Create a synthetic trading intention with no ownership or cash assumptions."""
    return AgentDecision(
        agent=AgentIdentity(
            agent_id=agent_id, agent_type=AgentType.TRADER, region=region
        ),
        month=date(2025, 1, 1),
        action=action,
        commodity=commodity,
        quantity_tonnes=quantity,
    )


@pytest.fixture
def seller() -> AgentDecision:
    """Offer one hundred synthetic tonnes from Brazil."""
    return _decision("seller", Region.BRAZIL, DecisionAction.OFFER_FOR_SALE, 100)


@pytest.fixture
def buyer() -> AgentDecision:
    """Request one hundred synthetic tonnes in Argentina."""
    return _decision("buyer", Region.ARGENTINA, DecisionAction.PURCHASE, 100)


@pytest.fixture
def inventory() -> Inventory:
    """Provide one hundred tonnes of shared Brazilian soybean stock."""
    return Inventory(
        region=Region.BRAZIL, commodity=Commodity.SOYBEANS, quantity_tonnes=100
    )


def test_public_exports() -> None:
    """Expose independent trade matching and its domain error through the market API."""
    assert match_interregional_trades is trading.match_interregional_trades
    assert TradeMatchingError is trading.TradeMatchingError
    assert issubclass(TradeMatchingError, ValueError)
    assert {"match_interregional_trades", "TradeMatchingError"} <= set(market.__all__)


@pytest.mark.parametrize(
    ("offered", "requested", "available", "expected"),
    [
        (100, 100, 100, 100),
        (100, 40, 200, 40),
        (30, 100, 200, 30),
        (100, 100, 25, 25),
        (0.3, 0.25, 0.2, 0.2),
    ],
)
def test_single_pair_is_limited_by_offer_demand_and_inventory(
    seller: AgentDecision,
    buyer: AgentDecision,
    inventory: Inventory,
    offered: float,
    requested: float,
    available: float,
    expected: float,
) -> None:
    """Build physical trade records for complete fills and each partial-fill limit."""
    result = match_interregional_trades(
        [
            seller.model_copy(update={"quantity_tonnes": offered}),
            buyer.model_copy(update={"quantity_tonnes": requested}),
        ],
        [inventory.model_copy(update={"quantity_tonnes": available})],
        month=seller.month,
    )
    assert result == (
        TradeExecution(
            month=seller.month,
            seller_agent_id=seller.agent.agent_id,
            buyer_agent_id=buyer.agent.agent_id,
            source_region=seller.agent.region,
            destination_region=buyer.agent.region,
            commodity=Commodity.SOYBEANS,
            quantity_tonnes=expected,
        ),
    )


def test_producer_and_trader_offers_share_regional_stock(
    buyer: AgentDecision, inventory: Inventory
) -> None:
    """Cap combined regional exports even when separate agents offer the same stock."""
    producer = _decision("a-producer", Region.BRAZIL, DecisionAction.OFFER_FOR_SALE, 80)
    producer = producer.model_copy(
        update={
            "agent": producer.agent.model_copy(
                update={"agent_type": AgentType.PRODUCER}
            )
        }
    )
    trader = _decision("z-trader", Region.BRAZIL, DecisionAction.OFFER_FOR_SALE, 80)
    result = match_interregional_trades(
        [trader, producer, buyer.model_copy(update={"quantity_tonnes": 200})],
        [inventory.model_copy(update={"quantity_tonnes": 90})],
        month=buyer.month,
    )
    assert [(item.seller_agent_id, item.quantity_tonnes) for item in result] == [
        ("a-producer", 80),
        ("z-trader", 10),
    ]


def test_one_seller_matches_buyers_by_region_then_id(
    seller: AgentDecision, inventory: Inventory
) -> None:
    """Prioritize buyer regions before IDs and split one offer across counterparties."""
    buyers = [
        _decision("a-us", Region.UNITED_STATES, DecisionAction.PURCHASE, 100),
        _decision("z-ar", Region.ARGENTINA, DecisionAction.PURCHASE, 60),
        _decision("a-ar", Region.ARGENTINA, DecisionAction.PURCHASE, 30),
    ]
    result = match_interregional_trades(
        [seller, *buyers], [inventory], month=seller.month
    )
    assert [(item.buyer_agent_id, item.quantity_tonnes) for item in result] == [
        ("a-ar", 30),
        ("z-ar", 60),
        ("a-us", 10),
    ]


def test_one_buyer_matches_multiple_sellers(inventory: Inventory) -> None:
    """Consume available seller offers in regional priority rather than ID order."""
    sellers = [
        _decision("a-br", Region.BRAZIL, DecisionAction.OFFER_FOR_SALE, 70),
        _decision("z-ar", Region.ARGENTINA, DecisionAction.OFFER_FOR_SALE, 40),
    ]
    buyer = _decision("buyer-us", Region.UNITED_STATES, DecisionAction.PURCHASE, 100)
    result = match_interregional_trades(
        [*sellers, buyer],
        [inventory, inventory.model_copy(update={"region": Region.ARGENTINA})],
        month=buyer.month,
    )
    assert [(item.seller_agent_id, item.quantity_tonnes) for item in result] == [
        ("z-ar", 40),
        ("a-br", 60),
    ]


def test_same_region_buyer_remains_eligible_for_later_seller(
    inventory: Inventory,
) -> None:
    """Skip domestic pairs without discarding demand eligible for another seller."""
    decisions = [
        _decision("seller-ar", Region.ARGENTINA, DecisionAction.OFFER_FOR_SALE, 5),
        _decision("seller-br", Region.BRAZIL, DecisionAction.OFFER_FOR_SALE, 7),
        _decision("buyer-ar", Region.ARGENTINA, DecisionAction.PURCHASE, 6),
        _decision("buyer-br", Region.BRAZIL, DecisionAction.PURCHASE, 8),
    ]
    result = match_interregional_trades(
        decisions,
        [inventory, inventory.model_copy(update={"region": Region.ARGENTINA})],
        month=decisions[0].month,
    )
    assert [
        (item.seller_agent_id, item.buyer_agent_id, item.quantity_tonnes)
        for item in result
    ] == [("seller-ar", "buyer-br", 5), ("seller-br", "buyer-ar", 6)]


def test_matches_all_commodities_in_sorted_order(inventory: Inventory) -> None:
    """Permit distinct commodity intentions per agent while keeping stocks separate."""
    decisions = [
        decision
        for commodity in Commodity
        for decision in (
            _decision(
                "seller", Region.BRAZIL, DecisionAction.OFFER_FOR_SALE, 100, commodity
            ),
            _decision(
                "buyer", Region.UNITED_STATES, DecisionAction.PURCHASE, 80, commodity
            ),
        )
    ]
    inventories = [
        inventory.model_copy(update={"commodity": commodity}) for commodity in Commodity
    ]
    result = match_interregional_trades(
        decisions, inventories, month=decisions[0].month
    )
    assert [(item.commodity, item.quantity_tonnes) for item in result] == [
        (commodity, 80) for commodity in sorted(Commodity)
    ]


@pytest.mark.parametrize("mismatch", ["region", "commodity"])
def test_incompatible_pairs_do_not_match(
    seller: AgentDecision, buyer: AgentDecision, inventory: Inventory, mismatch: str
) -> None:
    """Exclude domestic pairs and unrelated commodities even when inventory exists."""
    if mismatch == "region":
        buyer = buyer.model_copy(
            update={
                "agent": buyer.agent.model_copy(update={"region": seller.agent.region})
            }
        )
    else:
        buyer = buyer.model_copy(update={"commodity": Commodity.SOYBEAN_MEAL})
    assert (
        match_interregional_trades([seller, buyer], [inventory], month=seller.month)
        == ()
    )


@pytest.mark.parametrize("zero_field", ["offer", "demand", "stock", "missing_stock"])
def test_no_zero_quantity_or_unfunded_trades(
    seller: AgentDecision, buyer: AgentDecision, inventory: Inventory, zero_field: str
) -> None:
    """Treat explicit zero stock and missing stock as unavailable for matching."""
    if zero_field == "offer":
        seller = seller.model_copy(update={"quantity_tonnes": 0})
    elif zero_field == "demand":
        buyer = buyer.model_copy(update={"quantity_tonnes": 0})
    elif zero_field == "stock":
        inventory = inventory.model_copy(update={"quantity_tonnes": 0})
    inventories = [] if zero_field == "missing_stock" else [inventory]
    assert (
        match_interregional_trades([seller, buyer], inventories, month=seller.month)
        == ()
    )


def test_purchases_do_not_replenish_sellable_stock(inventory: Inventory) -> None:
    """Do not resell inbound matches when the starting regional stock is unavailable."""
    decisions = [
        _decision("seller-ar", Region.ARGENTINA, DecisionAction.OFFER_FOR_SALE, 10),
        _decision("seller-br", Region.BRAZIL, DecisionAction.OFFER_FOR_SALE, 10),
        _decision("buyer-br", Region.BRAZIL, DecisionAction.PURCHASE, 10),
        _decision("buyer-us", Region.UNITED_STATES, DecisionAction.PURCHASE, 10),
    ]
    result = match_interregional_trades(
        decisions,
        [
            inventory.model_copy(
                update={"region": Region.ARGENTINA, "quantity_tonnes": 10}
            ),
            inventory.model_copy(update={"quantity_tonnes": 0}),
        ],
        month=decisions[0].month,
    )
    assert [(item.seller_agent_id, item.buyer_agent_id) for item in result] == [
        ("seller-ar", "buyer-br")
    ]


def test_permutations_are_deterministic_and_inputs_unchanged(
    inventory: Inventory,
) -> None:
    """Check every ordering of a small batch, including single-pass input iterators."""
    decisions = [
        _decision("seller-ar", Region.ARGENTINA, DecisionAction.OFFER_FOR_SALE, 40),
        _decision("seller-br", Region.BRAZIL, DecisionAction.OFFER_FOR_SALE, 90),
        _decision("buyer-br", Region.BRAZIL, DecisionAction.PURCHASE, 60),
        _decision("buyer-us", Region.UNITED_STATES, DecisionAction.PURCHASE, 100),
    ]
    inventories = [
        inventory,
        inventory.model_copy(
            update={"region": Region.ARGENTINA, "quantity_tonnes": 30}
        ),
    ]
    original_decisions = [item.model_dump_json() for item in decisions]
    original_inventories = [item.model_dump_json() for item in inventories]
    expected = match_interregional_trades(
        decisions, inventories, month=decisions[0].month
    )
    assert [
        (item.seller_agent_id, item.buyer_agent_id, item.quantity_tonnes)
        for item in expected
    ] == [("seller-ar", "buyer-br", 30), ("seller-br", "buyer-us", 90)]
    for ordered_decisions in permutations(decisions):
        for ordered_inventories in permutations(inventories):
            assert (
                match_interregional_trades(
                    iter(ordered_decisions),
                    iter(ordered_inventories),
                    month=decisions[0].month,
                )
                == expected
            )
    assert [item.model_dump_json() for item in decisions] == original_decisions
    assert [item.model_dump_json() for item in inventories] == original_inventories


def test_fractional_residuals_remain_nonnegative(
    seller: AgentDecision, inventory: Inventory
) -> None:
    """Allocate fractional tonnes without rounding or creating zero-quantity records."""
    buyers = [
        _decision("a", Region.ARGENTINA, DecisionAction.PURCHASE, 0.1),
        _decision("b", Region.ARGENTINA, DecisionAction.PURCHASE, 0.2),
    ]
    result = match_interregional_trades(
        [seller.model_copy(update={"quantity_tonnes": 0.3}), *buyers],
        [inventory.model_copy(update={"quantity_tonnes": 0.3})],
        month=seller.month,
    )
    assert [item.quantity_tonnes for item in result] == pytest.approx([0.1, 0.2])
    assert all(item.quantity_tonnes > 0 for item in result)
    assert sum(item.quantity_tonnes for item in result) <= 0.3


@pytest.mark.parametrize(
    "action", [DecisionAction.OFFER_FOR_SALE, DecisionAction.PURCHASE]
)
@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_trade_intentions_are_rejected(
    seller: AgentDecision, action: DecisionAction, conflicting: bool
) -> None:
    """Reject identical and differing quantities under the same trade-intention key."""
    decision = seller.model_copy(update={"action": action})
    duplicate = decision.model_copy(
        update={"quantity_tonnes": 50} if conflicting else {}
    )
    with pytest.raises(TradeMatchingError, match=r"Duplicate trade intention.*seller"):
        match_interregional_trades([decision, duplicate], [], month=seller.month)


@pytest.mark.parametrize("defect", ["region", "agent_type"])
def test_conflicting_identities_are_rejected(
    seller: AgentDecision, defect: str
) -> None:
    """Require each agent ID to identify one stable role and region in the batch."""
    change = Region.ARGENTINA if defect == "region" else AgentType.PRODUCER
    other = seller.model_copy(
        update={
            "agent": seller.agent.model_copy(update={defect: change}),
            "action": DecisionAction.PURCHASE,
        }
    )
    with pytest.raises(TradeMatchingError, match=r"Conflicting identities.*seller"):
        match_interregional_trades([seller, other], [], month=seller.month)


@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_inventory_keys_are_rejected(
    inventory: Inventory, conflicting: bool
) -> None:
    """Reject repeated stocks even when values agree and no decisions are supplied."""
    duplicate = inventory.model_copy(
        update={"quantity_tonnes": 50} if conflicting else {}
    )
    with pytest.raises(
        TradeMatchingError, match="Duplicate inventory for brazil/soybeans"
    ):
        match_interregional_trades([], [inventory, duplicate], month=date(2025, 1, 1))


@pytest.mark.parametrize(
    "month", [date(2025, 1, 2), datetime(2025, 1, 1), "2025-01-01", None, 0]
)
def test_invalid_matching_month_is_rejected(month: object) -> None:
    """Require an actual first-of-month date even for otherwise empty inputs."""
    with pytest.raises(TradeMatchingError, match="first day of a month"):
        match_interregional_trades([], [], month=cast(date, month))


@pytest.mark.parametrize("action", list(DecisionAction))
def test_decisions_from_another_month_are_rejected(
    seller: AgentDecision, action: DecisionAction
) -> None:
    """Validate every decision month, including crushing requests that are ignored."""
    decision = seller.model_copy(update={"action": action, "month": date(2025, 2, 1)})
    with pytest.raises(TradeMatchingError, match=r"decision month.*requested month"):
        match_interregional_trades([decision], [], month=seller.month)


def test_valid_crushing_is_ignored(
    seller: AgentDecision, buyer: AgentDecision, inventory: Inventory
) -> None:
    """Neither consume stock nor create trades from valid physical crushing requests."""
    crushing = seller.model_copy(
        update={
            "agent": seller.agent.model_copy(
                update={"agent_id": "crusher", "agent_type": AgentType.CRUSHER}
            ),
            "action": DecisionAction.CRUSH,
        }
    )
    assert (
        match_interregional_trades(
            [crushing, crushing], [inventory], month=seller.month
        )
        == ()
    )
    assert match_interregional_trades(
        [seller, buyer, crushing], [inventory], month=seller.month
    ) == match_interregional_trades([seller, buyer], [inventory], month=seller.month)


def test_ignored_crushing_still_has_consistent_identity(seller: AgentDecision) -> None:
    """Do not let excluded crushing decisions hide conflicting agent registrations."""
    crushing = seller.model_copy(
        update={
            "agent": seller.agent.model_copy(update={"agent_type": AgentType.CRUSHER}),
            "action": DecisionAction.CRUSH,
        }
    )
    with pytest.raises(TradeMatchingError, match="Conflicting identities"):
        match_interregional_trades([seller, crushing], [], month=seller.month)


@pytest.mark.parametrize("kind", ["decision", "inventory"])
@pytest.mark.parametrize(
    "quantity", [-1, float("nan"), float("inf"), float("-inf"), True, "100", None]
)
def test_unchecked_quantities_are_revalidated(
    seller: AgentDecision, inventory: Inventory, kind: str, quantity: object
) -> None:
    """Reject invalid copied quantities before deciding whether any trade can match."""
    decisions = (
        [seller.model_copy(update={"quantity_tonnes": quantity})]
        if kind == "decision"
        else []
    )
    inventories = (
        [inventory.model_copy(update={"quantity_tonnes": quantity})]
        if kind == "inventory"
        else []
    )
    with pytest.raises(TradeMatchingError, match="quantity_tonnes"):
        match_interregional_trades(decisions, inventories, month=seller.month)


@pytest.mark.parametrize(
    "changes",
    [
        {"action": "unknown"},
        {"commodity": "unknown"},
        {"month": "2025-01-01"},
        {"action": DecisionAction.CRUSH, "commodity": Commodity.SOYBEAN_MEAL},
    ],
)
def test_invalid_copied_decisions_are_rejected(
    seller: AgentDecision, changes: dict[str, object]
) -> None:
    """Preserve existing action, commodity, and calendar validation rules."""
    with pytest.raises(TradeMatchingError, match="Invalid decision"):
        match_interregional_trades(
            [seller.model_copy(update=changes)], [], month=seller.month
        )


def test_nested_identity_is_revalidated(seller: AgentDecision) -> None:
    """Reject an invalid nested identity even when the outer decision was copied."""
    invalid = seller.model_copy(
        update={"agent": seller.agent.model_copy(update={"agent_id": " "})}
    )
    with pytest.raises(TradeMatchingError, match="agent_id"):
        match_interregional_trades([invalid], [], month=seller.month)


@pytest.mark.parametrize("kind", ["decision", "inventory"])
def test_wrong_record_types_are_rejected(kind: str) -> None:
    """Require typed model inputs rather than silently coercing arbitrary mappings."""
    decisions = [cast(AgentDecision, {})] if kind == "decision" else []
    inventories = [cast(Inventory, {})] if kind == "inventory" else []
    with pytest.raises(TradeMatchingError, match="must contain"):
        match_interregional_trades(decisions, inventories, month=date(2025, 1, 1))


def test_empty_decisions_and_inventories(inventory: Inventory) -> None:
    """Return no physical trades when no intentions are supplied."""
    assert match_interregional_trades([], [inventory], month=date(2025, 1, 1)) == ()
    assert match_interregional_trades(iter(()), iter(()), month=date(2025, 1, 1)) == ()
