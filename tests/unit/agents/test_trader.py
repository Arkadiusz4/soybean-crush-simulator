from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from soybean_sim.agents import (
    Agent,
    AgentIdentity,
    AgentType,
    DecisionAction,
    Trader,
    TraderConfig,
    TraderDecisionError,
)
from soybean_sim.agents import trader as implementation
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketSnapshot,
    Region,
)


@pytest.fixture
def trader() -> Trader:
    """Provide explicit, distinct reference prices for an aggregate Brazilian trader."""
    return Trader(
        identity=AgentIdentity(
            agent_id="trader-br-1", agent_type=AgentType.TRADER, region=Region.BRAZIL
        ),
        config=TraderConfig(
            reference_soybean_price_usd_per_tonne=400,
            reference_soybean_meal_price_usd_per_tonne=300,
            reference_soybean_oil_price_usd_per_tonne=1000,
            maximum_monthly_purchase_tonnes=1000,
        ),
    )


@pytest.fixture
def snapshot() -> MarketSnapshot:
    """Provide synthetic unsorted observations without regional soybean stock."""
    month = date(2021, 1, 1)
    return MarketSnapshot(
        month=month,
        prices=tuple(
            CommodityPrice(
                commodity=commodity,
                month=month,
                price_usd_per_tonne=price,
                data_source="synthetic trader fixture",
            )
            for commodity, price in (
                (Commodity.SOYBEAN_OIL, 1000),
                (Commodity.SOYBEANS, 400),
                (Commodity.SOYBEAN_MEAL, 300),
            )
        ),
        inventories=tuple(
            Inventory(region=region, commodity=commodity, quantity_tonnes=quantity)
            for region, commodity, quantity in (
                (Region.UNITED_STATES, Commodity.SOYBEAN_MEAL, 5000),
                (Region.BRAZIL, Commodity.SOYBEAN_OIL, 200),
                (Region.UNITED_STATES, Commodity.SOYBEAN_OIL, 9000),
                (Region.BRAZIL, Commodity.SOYBEAN_MEAL, 1000),
            )
        ),
    )


def _with_price(
    snapshot: MarketSnapshot, commodity: Commodity, market_price: float
) -> MarketSnapshot:
    """Replace one synthetic price while retaining the other validated observations."""
    return MarketSnapshot(
        month=snapshot.month,
        prices=tuple(
            price.model_copy(update={"price_usd_per_tonne": market_price})
            if price.commodity == commodity
            else price
            for price in snapshot.prices
        ),
        inventories=snapshot.inventories,
    )


def test_public_exports() -> None:
    """Expose the trader, configuration, and missing-observation exception."""
    assert Trader is implementation.Trader
    assert TraderConfig is implementation.TraderConfig
    assert TraderDecisionError is implementation.TraderDecisionError


@pytest.mark.parametrize(
    ("market_price", "expected_tonnes"),
    [
        (320, 1000),
        (359.99, 1000),
        (360, 500),
        (400, 500),
        (439.99, 500),
        (440, 200),
        (480, 200),
    ],
)
def test_purchase_bands(
    trader: Trader,
    snapshot: MarketSnapshot,
    market_price: float,
    expected_tonnes: float,
) -> None:
    """Reduce purchase demand as soybean prices cross the exact 0.90 and 1.10 bounds."""
    observed = _with_price(snapshot, Commodity.SOYBEANS, market_price)
    purchases = [
        item
        for item in trader.decide(observed)
        if item.action == DecisionAction.PURCHASE
    ]
    assert len(purchases) == 1
    assert purchases[0].commodity is Commodity.SOYBEANS
    assert purchases[0].quantity_tonnes == expected_tonnes


@pytest.mark.parametrize(
    ("commodity", "reference_price", "stock"),
    [(Commodity.SOYBEAN_MEAL, 300, 1000), (Commodity.SOYBEAN_OIL, 1000, 200)],
)
@pytest.mark.parametrize(
    ("ratio", "fraction"),
    [
        (0.8, 0.2),
        (0.899, 0.2),
        (0.9, 0.5),
        (1, 0.5),
        (1.099, 0.5),
        (1.1, 0.8),
        (1.2, 0.8),
    ],
)
def test_product_sale_bands(
    trader: Trader,
    snapshot: MarketSnapshot,
    commodity: Commodity,
    reference_price: float,
    stock: float,
    ratio: float,
    fraction: float,
) -> None:
    """Use each product's reference price and regional stock to scale sale offers."""
    observed = _with_price(snapshot, commodity, reference_price * ratio)
    sale = next(item for item in trader.decide(observed) if item.commodity == commodity)
    assert sale.action is DecisionAction.OFFER_FOR_SALE
    assert sale.quantity_tonnes == pytest.approx(stock * fraction)
    assert 0 < sale.quantity_tonnes <= stock


def test_decision_attribution_and_order(
    trader: Trader, snapshot: MarketSnapshot
) -> None:
    """Sort intentions by commodity/action while preserving the trader and month."""
    decisions = trader.decide(snapshot)
    assert [(item.commodity, item.action) for item in decisions] == [
        (Commodity.SOYBEAN_MEAL, DecisionAction.OFFER_FOR_SALE),
        (Commodity.SOYBEAN_OIL, DecisionAction.OFFER_FOR_SALE),
        (Commodity.SOYBEANS, DecisionAction.PURCHASE),
    ]
    assert all(item.agent is trader.identity for item in decisions)
    assert all(item.month == snapshot.month for item in decisions)
    reordered = MarketSnapshot(
        month=snapshot.month,
        prices=tuple(reversed(snapshot.prices)),
        inventories=tuple(reversed(snapshot.inventories)),
    )
    assert trader.decide(reordered) == decisions


@pytest.mark.parametrize("stock", [0, 1, 1000000])
def test_purchases_ignore_soybean_inventory(
    trader: Trader, snapshot: MarketSnapshot, stock: float
) -> None:
    """Treat purchases as demand even with no soybean inventory or known zero stock."""
    observed = MarketSnapshot(
        month=snapshot.month,
        prices=snapshot.prices,
        inventories=(
            *snapshot.inventories,
            Inventory(
                region=Region.BRAZIL,
                commodity=Commodity.SOYBEANS,
                quantity_tonnes=stock,
            ),
        ),
    )
    assert trader.decide(observed) == trader.decide(snapshot)


def test_zero_purchase_capacity(trader: Trader, snapshot: MarketSnapshot) -> None:
    """Omit soybean demand without suppressing positive product sale offers."""
    configured = Trader(
        identity=trader.identity,
        config=trader.config.model_copy(update={"maximum_monthly_purchase_tonnes": 0}),
    )
    decisions = configured.decide(snapshot)
    assert len(decisions) == 2
    assert all(item.action is DecisionAction.OFFER_FOR_SALE for item in decisions)


@pytest.mark.parametrize("commodity", [Commodity.SOYBEAN_MEAL, Commodity.SOYBEAN_OIL])
def test_zero_product_inventory(
    trader: Trader, snapshot: MarketSnapshot, commodity: Commodity
) -> None:
    """Omit only a product with known zero regional stock, retaining other requests."""
    observed = MarketSnapshot(
        month=snapshot.month,
        prices=snapshot.prices,
        inventories=tuple(
            stock.model_copy(update={"quantity_tonnes": 0})
            if stock.region == Region.BRAZIL and stock.commodity == commodity
            else stock
            for stock in snapshot.inventories
        ),
    )
    decisions = trader.decide(observed)
    assert len(decisions) == 2
    assert all(item.commodity != commodity for item in decisions)


@pytest.mark.parametrize("fraction", [0, 1])
def test_fraction_endpoints(
    trader: Trader, snapshot: MarketSnapshot, fraction: float
) -> None:
    """Permit abstention, full purchase capacity, and full product stock offers."""
    configured = Trader(
        identity=trader.identity,
        config=TraderConfig.model_validate(
            {
                **trader.config.model_dump(),
                "low_purchase_fraction": fraction,
                "normal_purchase_fraction": fraction,
                "high_purchase_fraction": fraction,
                "low_sale_fraction": fraction,
                "normal_sale_fraction": fraction,
                "high_sale_fraction": fraction,
            }
        ),
    )
    decisions = configured.decide(snapshot)
    if fraction == 0:
        assert decisions == ()
    else:
        assert {item.commodity: item.quantity_tonnes for item in decisions} == {
            Commodity.SOYBEANS: 1000,
            Commodity.SOYBEAN_MEAL: 1000,
            Commodity.SOYBEAN_OIL: 200,
        }


@pytest.mark.parametrize(
    ("soybeans", "meal", "oil", "purchase", "meal_sale", "oil_sale"),
    [
        (350, 300, 500, 900, 100, 20),
        (400, 320, 640, 400, 400, 80),
        (600, 480, 960, 100, 900, 180),
    ],
)
def test_custom_response(
    trader: Trader,
    snapshot: MarketSnapshot,
    soybeans: float,
    meal: float,
    oil: float,
    purchase: float,
    meal_sale: float,
    oil_sale: float,
) -> None:
    """Use custom reference prices, thresholds, and response fractions."""
    configured = Trader(
        identity=trader.identity,
        config=TraderConfig(
            reference_soybean_price_usd_per_tonne=500,
            reference_soybean_meal_price_usd_per_tonne=400,
            reference_soybean_oil_price_usd_per_tonne=800,
            maximum_monthly_purchase_tonnes=1000,
            low_price_ratio=0.8,
            high_price_ratio=1.2,
            low_purchase_fraction=0.9,
            normal_purchase_fraction=0.4,
            high_purchase_fraction=0.1,
            low_sale_fraction=0.1,
            normal_sale_fraction=0.4,
            high_sale_fraction=0.9,
        ),
    )
    observed = snapshot
    for commodity, price in (
        (Commodity.SOYBEANS, soybeans),
        (Commodity.SOYBEAN_MEAL, meal),
        (Commodity.SOYBEAN_OIL, oil),
    ):
        observed = _with_price(observed, commodity, price)
    assert {
        item.commodity: item.quantity_tonnes for item in configured.decide(observed)
    } == {
        Commodity.SOYBEANS: purchase,
        Commodity.SOYBEAN_MEAL: meal_sale,
        Commodity.SOYBEAN_OIL: oil_sale,
    }


@pytest.mark.parametrize("commodity", list(Commodity))
@pytest.mark.parametrize("inactive", [False, True])
def test_missing_required_prices(
    trader: Trader, snapshot: MarketSnapshot, commodity: Commodity, inactive: bool
) -> None:
    """Require prices even with zero purchase capacity and known zero product stocks."""
    configured = Trader(
        identity=trader.identity,
        config=trader.config.model_copy(update={"maximum_monthly_purchase_tonnes": 0})
        if inactive
        else trader.config,
    )
    observed = MarketSnapshot(
        month=snapshot.month,
        prices=tuple(
            price for price in snapshot.prices if price.commodity != commodity
        ),
        inventories=tuple(
            stock.model_copy(update={"quantity_tonnes": 0}) if inactive else stock
            for stock in snapshot.inventories
        ),
    )
    with pytest.raises(
        TraderDecisionError, match=f"missing global price for {commodity.value}"
    ) as error:
        configured.decide(observed)
    assert trader.identity.agent_id in str(error.value)
    assert snapshot.month.isoformat() in str(error.value)


@pytest.mark.parametrize("commodity", [Commodity.SOYBEAN_MEAL, Commodity.SOYBEAN_OIL])
@pytest.mark.parametrize("inactive", [False, True])
def test_missing_regional_product_inventory(
    trader: Trader, snapshot: MarketSnapshot, commodity: Commodity, inactive: bool
) -> None:
    """Require regional product stocks even during inactivity."""
    configured = Trader(
        identity=trader.identity,
        config=TraderConfig.model_validate(
            {
                **trader.config.model_dump(),
                "maximum_monthly_purchase_tonnes": 0,
                "low_sale_fraction": 0,
                "normal_sale_fraction": 0,
                "high_sale_fraction": 0,
            }
        )
        if inactive
        else trader.config,
    )
    observed = MarketSnapshot(
        month=snapshot.month,
        prices=snapshot.prices,
        inventories=tuple(
            stock
            for stock in snapshot.inventories
            if not (stock.region == Region.BRAZIL and stock.commodity == commodity)
        ),
    )
    with pytest.raises(
        TraderDecisionError, match=f"missing {commodity.value} inventory"
    ) as error:
        configured.decide(observed)
    assert trader.identity.agent_id in str(error.value)
    assert "brazil" in str(error.value)
    assert snapshot.month.isoformat() in str(error.value)


def test_deterministic_without_mutation(
    trader: Trader, snapshot: MarketSnapshot
) -> None:
    """Repeat intentions without reserving stocks or executing commodity transfers."""
    original_snapshot = snapshot.model_dump()
    original_config = trader.config.model_dump()
    decisions = trader.decide(snapshot)
    assert trader.decide(snapshot) == decisions
    assert snapshot.model_dump() == original_snapshot
    assert trader.config.model_dump() == original_config


def test_agent_protocol(trader: Trader, snapshot: MarketSnapshot) -> None:
    """Use Trader structurally through Agent without requiring inheritance."""
    agent: Agent = trader
    assert agent.identity is trader.identity
    assert agent.decide(snapshot) == trader.decide(snapshot)


@pytest.mark.parametrize("agent_type", [AgentType.PRODUCER, AgentType.CRUSHER])
def test_rejects_nontrader_identity(trader: Trader, agent_type: AgentType) -> None:
    """Reject an incompatible role rather than rewriting its identity."""
    identity = AgentIdentity(
        agent_id="wrong-role", agent_type=agent_type, region=Region.BRAZIL
    )
    with pytest.raises(ValueError, match="agent_type='trader'"):
        Trader(identity=identity, config=trader.config)


@pytest.mark.parametrize("field", ["identity", "config"])
def test_stable_identity_and_config(trader: Trader, field: str) -> None:
    """Prevent replacing a trader's identity or validated configuration."""
    with pytest.raises(FrozenInstanceError):
        setattr(trader, field, getattr(trader, field))
