from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from soybean_sim.agents import (
    Agent,
    AgentIdentity,
    AgentType,
    DecisionAction,
    Producer,
    ProducerConfig,
    ProducerDecisionError,
)
from soybean_sim.agents import producer as implementation
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketSnapshot,
    Region,
)


@pytest.fixture
def producer() -> Producer:
    """Provide an aggregate Brazilian producer with an explicit USD/tonne baseline."""
    return Producer(
        identity=AgentIdentity(
            agent_id="producer-br-1",
            agent_type=AgentType.PRODUCER,
            region=Region.BRAZIL,
        ),
        config=ProducerConfig(reference_price_usd_per_tonne=400),
    )


def _snapshot(
    market_price: float = 400, available_tonnes: float = 100
) -> MarketSnapshot:
    """Provide synthetic observations with unrelated prices and inventories first."""
    month = date(2021, 1, 1)
    return MarketSnapshot(
        month=month,
        prices=(
            CommodityPrice(
                commodity=Commodity.SOYBEAN_MEAL,
                month=month,
                price_usd_per_tonne=600,
                data_source="synthetic producer fixture",
            ),
            CommodityPrice(
                commodity=Commodity.SOYBEANS,
                month=month,
                price_usd_per_tonne=market_price,
                data_source="synthetic producer fixture",
            ),
        ),
        inventories=(
            Inventory(
                region=Region.UNITED_STATES,
                commodity=Commodity.SOYBEANS,
                quantity_tonnes=10000,
            ),
            Inventory(
                region=Region.BRAZIL,
                commodity=Commodity.SOYBEAN_MEAL,
                quantity_tonnes=2000,
            ),
            Inventory(
                region=Region.BRAZIL,
                commodity=Commodity.SOYBEANS,
                quantity_tonnes=available_tonnes,
            ),
        ),
    )


def test_public_exports() -> None:
    """Expose the producer, its configuration, and missing-observation error."""
    assert Producer is implementation.Producer
    assert ProducerConfig is implementation.ProducerConfig
    assert ProducerDecisionError is implementation.ProducerDecisionError


@pytest.mark.parametrize(
    ("market_price", "offered_tonnes"),
    [(300, 20), (359.99, 20), (360, 50), (400, 50), (439.99, 50), (440, 80), (500, 80)],
)
def test_default_price_bands(
    producer: Producer, market_price: float, offered_tonnes: float
) -> None:
    """Apply low, normal, and high fractions with inclusive lower band boundaries."""
    snapshot = _snapshot(market_price)
    decisions = producer.decide(snapshot)
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.quantity_tonnes == pytest.approx(offered_tonnes)
    assert decision.agent is producer.identity
    assert decision.month == snapshot.month
    assert decision.action is DecisionAction.OFFER_FOR_SALE
    assert decision.commodity is Commodity.SOYBEANS


@pytest.mark.parametrize(
    ("market_price", "quantity"), [(350, 10), (400, 40), (600, 90)]
)
def test_custom_configuration(
    producer: Producer, market_price: float, quantity: float
) -> None:
    """Use supplied reference prices, thresholds, and fractions rather than defaults."""
    configured = Producer(
        identity=producer.identity,
        config=ProducerConfig(
            reference_price_usd_per_tonne=500,
            low_price_ratio=0.8,
            high_price_ratio=1.2,
            low_supply_fraction=0.1,
            normal_supply_fraction=0.4,
            high_supply_fraction=0.9,
        ),
    )
    assert configured.decide(_snapshot(market_price))[
        0
    ].quantity_tonnes == pytest.approx(quantity)


def test_zero_inventory_abstains(producer: Producer) -> None:
    """Abstain for known empty stock despite other available inventories."""
    assert producer.decide(_snapshot(available_tonnes=0)) == ()


@pytest.mark.parametrize("quantity", [0, 100])
def test_missing_soybean_price(producer: Producer, quantity: float) -> None:
    """Reject a missing soybean price even when known regional stock is zero."""
    snapshot = _snapshot(available_tonnes=quantity)
    observed = MarketSnapshot(
        month=snapshot.month,
        prices=(snapshot.prices[0],),
        inventories=snapshot.inventories,
    )
    with pytest.raises(
        ProducerDecisionError, match="missing global soybean price"
    ) as error:
        producer.decide(observed)
    assert producer.identity.agent_id in str(error.value)
    assert snapshot.month.isoformat() in str(error.value)


@pytest.mark.parametrize(
    "inventory_scope", ["empty", "other_region", "other_commodity"]
)
def test_missing_regional_soybeans(producer: Producer, inventory_scope: str) -> None:
    """Distinguish missing regional soybeans from zero or unrelated stock."""
    snapshot = _snapshot()
    inventories = {
        "empty": (),
        "other_region": (snapshot.inventories[0],),
        "other_commodity": (snapshot.inventories[1],),
    }
    observed = MarketSnapshot(
        month=snapshot.month,
        prices=snapshot.prices,
        inventories=inventories[inventory_scope],
    )
    with pytest.raises(
        ProducerDecisionError, match="missing soybean inventory"
    ) as error:
        producer.decide(observed)
    assert producer.identity.agent_id in str(error.value)
    assert "brazil" in str(error.value)
    assert snapshot.month.isoformat() in str(error.value)


@pytest.mark.parametrize("fraction", [0, 1])
def test_fraction_endpoints(producer: Producer, fraction: float) -> None:
    """Allow explicit zero-tonne intentions and offers of all available stock."""
    configured = Producer(
        identity=producer.identity,
        config=ProducerConfig(
            reference_price_usd_per_tonne=400,
            low_supply_fraction=fraction,
            normal_supply_fraction=fraction,
            high_supply_fraction=fraction,
        ),
    )
    decisions = configured.decide(_snapshot())
    assert len(decisions) == 1
    assert decisions[0].quantity_tonnes == 100 * fraction


def test_preserves_fractional_tonnes(producer: Producer) -> None:
    """Multiply available stocks without rounding physical quantities."""
    snapshot = _snapshot(market_price=300, available_tonnes=123.456789)
    assert producer.decide(snapshot)[0].quantity_tonnes == 123.456789 * 0.2


def test_deterministic_without_mutation(producer: Producer) -> None:
    """Repeat intentions without changing observations or configuration."""
    snapshot = _snapshot()
    original_snapshot = snapshot.model_dump()
    original_config = producer.config.model_dump()
    first = producer.decide(snapshot)
    assert first == producer.decide(snapshot)
    assert snapshot.model_dump() == original_snapshot
    assert producer.config.model_dump() == original_config


def test_agent_protocol(producer: Producer) -> None:
    """Use Producer structurally through Agent without inheriting its protocol."""
    agent: Agent = producer
    assert agent.identity is producer.identity
    assert agent.decide(_snapshot()) == producer.decide(_snapshot())


@pytest.mark.parametrize("agent_type", [AgentType.CRUSHER, AgentType.TRADER])
def test_rejects_nonproducer_identity(
    producer: Producer, agent_type: AgentType
) -> None:
    """Require an explicit producer role rather than rewriting the supplied identity."""
    identity = AgentIdentity(
        agent_id="wrong-role", agent_type=agent_type, region=Region.BRAZIL
    )
    with pytest.raises(ValueError, match="agent_type='producer'"):
        Producer(identity=identity, config=producer.config)


@pytest.mark.parametrize("field", ["identity", "config"])
def test_stable_identity_and_config(producer: Producer, field: str) -> None:
    """Prevent replacing a producer's identity or configuration after creation."""
    with pytest.raises(FrozenInstanceError):
        setattr(producer, field, getattr(producer, field))
