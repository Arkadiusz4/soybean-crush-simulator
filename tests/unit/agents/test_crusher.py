from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from soybean_sim.agents import (
    Agent,
    AgentIdentity,
    AgentType,
    Crusher,
    CrusherConfig,
    CrusherDecisionError,
    DecisionAction,
)
from soybean_sim.agents import crusher as implementation
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketSnapshot,
    Region,
)


@pytest.fixture
def crusher() -> Crusher:
    """Provide synthetic Brazilian processing capacity and per-input-tonne costs."""
    return Crusher(
        identity=AgentIdentity(
            agent_id="crusher-br-1", agent_type=AgentType.CRUSHER, region=Region.BRAZIL
        ),
        config=CrusherConfig(
            monthly_capacity_tonnes=1000, processing_cost_usd_per_tonne=10
        ),
    )


def _snapshot(soybean_price: float = 400, stock_tonnes: float = 2000) -> MarketSnapshot:
    """Provide synthetic prices and stocks, with unrelated regional records first."""
    month = date(2021, 1, 1)
    return MarketSnapshot(
        month=month,
        prices=tuple(
            CommodityPrice(
                commodity=commodity,
                month=month,
                price_usd_per_tonne=price,
                data_source="synthetic crusher fixture",
            )
            for commodity, price in (
                (Commodity.SOYBEAN_OIL, 600),
                (Commodity.SOYBEANS, soybean_price),
                (Commodity.SOYBEAN_MEAL, 400),
            )
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
                quantity_tonnes=5000,
            ),
            Inventory(
                region=Region.BRAZIL,
                commodity=Commodity.SOYBEANS,
                quantity_tonnes=stock_tonnes,
            ),
        ),
    )


def test_public_exports() -> None:
    """Expose the crusher, its configuration, and its decision error."""
    assert Crusher is implementation.Crusher
    assert CrusherConfig is implementation.CrusherConfig
    assert CrusherDecisionError is implementation.CrusherDecisionError


@pytest.mark.parametrize(
    ("net_margin", "requested_tonnes"),
    [
        (-10, 0),
        (0, 0),
        (1, 300),
        (29, 300),
        (30, 600),
        (31, 600),
        (59, 600),
        (60, 1000),
        (61, 1000),
    ],
)
def test_margin_bands(
    crusher: Crusher, net_margin: float, requested_tonnes: float
) -> None:
    """Apply the default margin boundaries to tonnes of soybean input."""
    # Weighted revenue is 440 USD/input tonne; processing costs are 10.
    snapshot = _snapshot(soybean_price=430 - net_margin)
    decisions = crusher.decide(snapshot)
    if requested_tonnes == 0:
        assert decisions == ()
        return
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.quantity_tonnes == pytest.approx(requested_tonnes)
    assert decision.agent is crusher.identity
    assert decision.month == snapshot.month
    assert decision.action is DecisionAction.CRUSH
    assert decision.commodity is Commodity.SOYBEANS


@pytest.mark.parametrize(
    ("processing_cost", "requested_tonnes"), [(0, 600), (10, 600), (20, 300), (40, 0)]
)
def test_weighted_spread_and_processing_costs(
    crusher: Crusher, processing_cost: float, requested_tonnes: float
) -> None:
    """Deduct processing costs from a 40 USD/tonne gross spread before choosing use."""
    config = CrusherConfig(
        monthly_capacity_tonnes=1000,
        processing_cost_usd_per_tonne=processing_cost,
    )
    configured = Crusher(identity=crusher.identity, config=config)
    # 0.8 * 400 + 0.2 * 600 - 400 = 40 USD per soybean input tonne.
    decisions = configured.decide(_snapshot())
    assert sum(decision.quantity_tonnes for decision in decisions) == requested_tonnes


def test_configured_yields_affect_margin(crusher: Crusher) -> None:
    """Use configured product yields when calculating weighted output revenue."""
    configured = Crusher(
        identity=crusher.identity,
        config=CrusherConfig(
            monthly_capacity_tonnes=1000,
            processing_cost_usd_per_tonne=10,
            meal_yield_fraction=0.5,
            oil_yield_fraction=0.5,
        ),
    )
    # 0.5 * 400 + 0.5 * 600 - 400 - 10 = 90: full utilization.
    assert configured.decide(_snapshot())[0].quantity_tonnes == 1000


@pytest.mark.parametrize(
    ("margin", "requested_tonnes"), [(-10, 0), (0, 100), (10, 400), (20, 800)]
)
def test_configured_margin_response(
    crusher: Crusher, margin: float, requested_tonnes: float
) -> None:
    """Use configured utilization bands, allowing explicit negative thresholds."""
    configured = Crusher(
        identity=crusher.identity,
        config=CrusherConfig(
            monthly_capacity_tonnes=1000,
            processing_cost_usd_per_tonne=10,
            low_margin_threshold_usd_per_tonne=-10,
            high_margin_threshold_usd_per_tonne=10,
            full_utilization_threshold_usd_per_tonne=20,
            low_utilization_fraction=0.1,
            medium_utilization_fraction=0.4,
            high_utilization_fraction=0.8,
        ),
    )
    decisions = configured.decide(_snapshot(soybean_price=430 - margin))
    assert sum(decision.quantity_tonnes for decision in decisions) == requested_tonnes


@pytest.mark.parametrize(
    ("capacity", "stock", "expected"),
    [(1000, 2000, 1000), (250, 2000, 250), (1000, 125.123456, 125.123456)],
)
def test_capacity_and_regional_inventory_bounds(
    crusher: Crusher, capacity: float, stock: float, expected: float
) -> None:
    """Cap requested input without rounding or claiming unrelated regional stock."""
    configured = Crusher(
        identity=crusher.identity,
        config=CrusherConfig(
            monthly_capacity_tonnes=capacity, processing_cost_usd_per_tonne=10
        ),
    )
    assert (
        configured.decide(_snapshot(soybean_price=300, stock_tonnes=stock))[
            0
        ].quantity_tonnes
        == expected
    )


def test_zero_stock_abstains(crusher: Crusher) -> None:
    """Request nothing when regional soybeans are explicitly zero."""
    assert crusher.decide(_snapshot(stock_tonnes=0)) == ()


def test_zero_utilization_abstains(crusher: Crusher) -> None:
    """Request nothing when a configured active margin band uses zero capacity."""
    configured = Crusher(
        identity=crusher.identity,
        config=CrusherConfig(
            monthly_capacity_tonnes=1000,
            processing_cost_usd_per_tonne=10,
            low_utilization_fraction=0,
            medium_utilization_fraction=0,
            high_utilization_fraction=0,
        ),
    )
    assert configured.decide(_snapshot()) == ()


@pytest.mark.parametrize("commodity", list(Commodity))
@pytest.mark.parametrize("stock", [0, 2000])
def test_missing_prices(crusher: Crusher, commodity: Commodity, stock: float) -> None:
    """Require each global price even if the regional stock is known to be zero."""
    snapshot = _snapshot(stock_tonnes=stock)
    observed = MarketSnapshot(
        month=snapshot.month,
        prices=tuple(
            price for price in snapshot.prices if price.commodity != commodity
        ),
        inventories=snapshot.inventories,
    )
    with pytest.raises(
        CrusherDecisionError, match=f"missing global price for {commodity.value}"
    ) as error:
        crusher.decide(observed)
    assert crusher.identity.agent_id in str(error.value)
    assert snapshot.month.isoformat() in str(error.value)


@pytest.mark.parametrize("scope", ["empty", "other_region", "other_commodity"])
@pytest.mark.parametrize("soybean_price", [400, 500])
def test_missing_regional_soybeans(
    crusher: Crusher, scope: str, soybean_price: float
) -> None:
    """Reject unknown regional stock even when the margin would prevent processing."""
    snapshot = _snapshot(soybean_price=soybean_price)
    inventories = {
        "empty": (),
        "other_region": (snapshot.inventories[0],),
        "other_commodity": (snapshot.inventories[1],),
    }
    observed = MarketSnapshot(
        month=snapshot.month, prices=snapshot.prices, inventories=inventories[scope]
    )
    with pytest.raises(
        CrusherDecisionError, match="missing soybean inventory"
    ) as error:
        crusher.decide(observed)
    assert "brazil" in str(error.value)
    assert crusher.identity.agent_id in str(error.value)
    assert snapshot.month.isoformat() in str(error.value)


def test_nonfinite_computed_margin(crusher: Crusher) -> None:
    """Report overflow from finite inputs rather than using an infinite margin."""
    configured = Crusher(
        identity=crusher.identity,
        config=CrusherConfig(
            monthly_capacity_tonnes=1000, processing_cost_usd_per_tonne=1.7e308
        ),
    )
    with pytest.raises(CrusherDecisionError, match="nonfinite crush margin"):
        configured.decide(_snapshot(soybean_price=1.7e308))


def test_deterministic_without_mutation(crusher: Crusher) -> None:
    """Repeat requests without consuming input stock or creating output inventories."""
    snapshot = _snapshot()
    original_snapshot = snapshot.model_dump()
    original_config = crusher.config.model_dump()
    first = crusher.decide(snapshot)
    assert first == crusher.decide(snapshot)
    assert snapshot.model_dump() == original_snapshot
    assert crusher.config.model_dump() == original_config


def test_agent_protocol(crusher: Crusher) -> None:
    """Use Crusher structurally through Agent without requiring inheritance."""
    agent: Agent = crusher
    assert agent.identity is crusher.identity
    assert agent.decide(_snapshot()) == crusher.decide(_snapshot())


@pytest.mark.parametrize("agent_type", [AgentType.PRODUCER, AgentType.TRADER])
def test_rejects_noncrusher_identity(crusher: Crusher, agent_type: AgentType) -> None:
    """Reject an incompatible role without silently rewriting the supplied identity."""
    identity = AgentIdentity(
        agent_id="wrong-role", agent_type=agent_type, region=Region.BRAZIL
    )
    with pytest.raises(ValueError, match="agent_type='crusher'"):
        Crusher(identity=identity, config=crusher.config)


@pytest.mark.parametrize("field", ["identity", "config"])
def test_stable_identity_and_configuration(crusher: Crusher, field: str) -> None:
    """Prevent replacing an aggregate crusher's identity or validated settings."""
    with pytest.raises(FrozenInstanceError):
        setattr(crusher, field, getattr(crusher, field))
