import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest

from soybean_sim.agents import (
    AgentDecision,
    AgentIdentity,
    AgentType,
    CrusherConfig,
    DecisionAction,
)
from soybean_sim.market import (
    MarketFlow,
    MarketFlowType,
    MarketState,
    PriceDynamicsConfig,
    PriceDynamicsError,
    RegionalPriceError,
    initialize_regional_prices,
    update_regional_prices,
)
from soybean_sim.models import Commodity, Inventory, MarketSnapshot, Region
from soybean_sim.simulation import SimulationError, run_simulation


@dataclass
class _RecordingAgent:
    """Record price observations while submitting fixed synthetic intentions."""

    identity: AgentIdentity
    intentions: tuple[tuple[DecisionAction, Commodity, float], ...] = ()
    observations: list[MarketSnapshot] = field(default_factory=list)

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Record the observed month and prices without changing shared stocks.

        Args:
            snapshot (MarketSnapshot): Current agent view after production.

        Returns:
            tuple[AgentDecision, ...]: Current-month test intentions.
        """
        self.observations.append(snapshot)
        return tuple(
            AgentDecision(
                agent=self.identity,
                month=snapshot.month,
                action=action,
                commodity=commodity,
                quantity_tonnes=quantity,
            )
            for action, commodity, quantity in self.intentions
        )


@pytest.fixture
def observer() -> _RecordingAgent:
    """Provide a Brazilian trader that records observations and abstains.

    Returns:
        _RecordingAgent: Fresh observer with no market intentions.
    """
    return _RecordingAgent(
        AgentIdentity(
            agent_id="observer", agent_type=AgentType.TRADER, region=Region.BRAZIL
        )
    )


@pytest.fixture
def two_regions(state: MarketState) -> MarketState:
    """Include an inactive US region to check full inventory-region coverage.

    Args:
        state (MarketState): Synthetic Brazilian market.

    Returns:
        MarketState: Two regions with all three commodity inventories.
    """
    return MarketState.from_snapshot(
        state.snapshot.model_copy(
            update={
                "inventories": (
                    *state.snapshot.inventories,
                    *(
                        Inventory(
                            region=Region.UNITED_STATES,
                            commodity=commodity,
                            quantity_tonnes=0,
                        )
                        for commodity in sorted(Commodity)
                    ),
                )
            }
        )
    )


@pytest.mark.parametrize("physical", [False, True])
@pytest.mark.parametrize("regional", [False, True])
def test_disabled_dynamics_preserves_existing_modes(
    state: MarketState, observer: _RecordingAgent, physical: bool, regional: bool
) -> None:
    """Keep omitted and explicitly disabled dynamics equivalent in all old modes.

    Args:
        state (MarketState): Initial synthetic market.
        observer (_RecordingAgent): Price observer.
        physical (bool): Whether to enable the physical cycle.
        regional (bool): Whether to supply regional baselines.

    Returns:
        None: Confirms constant observations, stock continuity, and empty pressure.
    """
    prices = (
        initialize_regional_prices(state.snapshot, [Region.BRAZIL])
        if regional
        else None
    )
    flows = () if physical else None
    result = run_simulation(
        state,
        [observer],
        crusher_configs={},
        months=2,
        regional_prices=prices,
        monthly_flows=flows,
    )
    assert result == run_simulation(
        state,
        [observer],
        crusher_configs={},
        months=2,
        regional_prices=prices,
        monthly_flows=flows,
        price_dynamics_config=None,
    )
    assert result.final_state.snapshot.inventories == state.snapshot.inventories
    for monthly in result.monthly_results:
        assert monthly.physical_cycle == physical
        assert monthly.market_pressures == ()
        assert bool(monthly.regional_prices) == regional
        assert all(price.month == monthly.month for price in monthly.regional_prices)
        assert [price.price_usd_per_tonne for price in monthly.regional_prices] == (
            [price.price_usd_per_tonne for price in prices] if prices else []
        )


def test_dynamic_pricing_requires_physical_mode(
    state: MarketState, observer: _RecordingAgent
) -> None:
    """Reject implicit physical activation before invoking any agent.

    Args:
        state (MarketState): Initial market.
        observer (_RecordingAgent): Observer used to detect premature execution.

    Returns:
        None: Confirms a descriptive error and no calls.
    """
    with pytest.raises(SimulationError, match="requires monthly_flows"):
        run_simulation(
            state,
            [observer],
            crusher_configs={},
            months=1,
            price_dynamics_config=PriceDynamicsConfig(),
        )
    assert observer.observations == []


def test_automatic_prices_cover_inactive_regions(
    two_regions: MarketState, observer: _RecordingAgent
) -> None:
    """Initialize every inventory region and produce zero pressures for inactivity.

    Args:
        two_regions (MarketState): State including a region without agents.
        observer (_RecordingAgent): Brazilian observer.

    Returns:
        None: Confirms full price/pressure coverage and unchanged zero-activity prices.
    """
    result = run_simulation(
        two_regions,
        [observer],
        crusher_configs={},
        months=2,
        monthly_flows=iter(()),
        price_dynamics_config=PriceDynamicsConfig(),
    )
    expected = initialize_regional_prices(
        two_regions.snapshot, [Region.BRAZIL, Region.UNITED_STATES]
    )
    assert result.monthly_results[0].regional_prices == expected
    for monthly in result.monthly_results:
        assert len(monthly.regional_prices) == len(monthly.market_pressures) == 6
        assert all(
            pressure.supply_tonnes == pressure.demand_tonnes == 0
            for pressure in monthly.market_pressures
        )
        assert [price.price_usd_per_tonne for price in monthly.regional_prices] == [
            price.price_usd_per_tonne for price in expected
        ]
        assert all(
            pressure.month == monthly.month for pressure in monthly.market_pressures
        )
    assert [snapshot.month for snapshot in observer.observations] == [
        date(2023, 11, 1),
        date(2023, 12, 1),
    ]


def test_explicit_baselines_are_not_overwritten(
    state: MarketState, observer: _RecordingAgent
) -> None:
    """Respect explicit regional values while retaining original global references.

    Args:
        state (MarketState): Initial global references.
        observer (_RecordingAgent): Observer of custom regional values.

    Returns:
        None: Confirms baseline values are used in the first and next month.
    """
    prices = tuple(
        price.model_copy(update={"price_usd_per_tonne": price.price_usd_per_tonne * 2})
        for price in initialize_regional_prices(state.snapshot, [Region.BRAZIL])
    )
    result = run_simulation(
        state,
        [observer],
        crusher_configs={},
        months=2,
        regional_prices=reversed(prices),
        monthly_flows=(),
        price_dynamics_config=PriceDynamicsConfig(),
    )
    assert result.monthly_results[0].regional_prices == prices
    for snapshot in observer.observations:
        assert {
            price.commodity: price.price_usd_per_tonne for price in snapshot.prices
        } == {price.commodity: price.price_usd_per_tonne for price in prices}
    assert (
        result.monthly_results[0].ending_state.snapshot.prices == state.snapshot.prices
    )


@pytest.mark.parametrize(
    "defect",
    [
        "missing_region",
        "missing_commodity",
        "empty",
        "duplicate",
        "month",
        "negative",
        "nonfinite",
        "extra_region",
    ],
)
def test_invalid_dynamic_price_coverage_fails_before_decisions(
    two_regions: MarketState, observer: _RecordingAgent, defect: str
) -> None:
    """Validate explicit coverage beyond active agent regions before execution.

    Args:
        two_regions (MarketState): Two represented regions.
        observer (_RecordingAgent): Observer checking fail-fast behavior.
        defect (str): Price coverage or record defect.

    Returns:
        None: Confirms domain errors without calls or partial history.
    """
    prices = initialize_regional_prices(
        two_regions.snapshot, [Region.BRAZIL, Region.UNITED_STATES]
    )
    if defect == "missing_region":
        prices = tuple(price for price in prices if price.region == Region.BRAZIL)
    elif defect == "missing_commodity":
        prices = prices[:-1]
    elif defect == "empty":
        prices = ()
    elif defect == "duplicate":
        prices = (*prices, prices[0])
    elif defect == "extra_region":
        prices = (*prices, prices[0].model_copy(update={"region": Region.ARGENTINA}))
    else:
        changes = {
            "month": {"month": date(2023, 12, 1)},
            "negative": {"price_usd_per_tonne": -1},
            "nonfinite": {"price_usd_per_tonne": math.nan},
        }
        prices = (prices[0].model_copy(update=changes[defect]), *prices[1:])
    with pytest.raises(RegionalPriceError):
        run_simulation(
            two_regions,
            [observer],
            crusher_configs={},
            months=2,
            regional_prices=prices,
            monthly_flows=(),
            price_dynamics_config=PriceDynamicsConfig(),
        )
    assert not observer.observations


@pytest.mark.parametrize(
    "config",
    [{}, PriceDynamicsConfig().model_copy(update={"price_sensitivity": math.inf})],
)
def test_invalid_dynamics_settings_fail_before_decisions(
    state: MarketState, observer: _RecordingAgent, config: Any
) -> None:
    """Revalidate typed settings rather than executing with malformed copies.

    Args:
        state (MarketState): Initial market.
        observer (_RecordingAgent): Observer checking fail-fast validation.
        config (Any): Malformed configuration supplied through the public API.

    Returns:
        None: Confirms a pricing-domain error before decisions.
    """
    with pytest.raises(PriceDynamicsError, match="config"):
        run_simulation(
            state,
            [observer],
            crusher_configs={},
            months=1,
            monthly_flows=(),
            price_dynamics_config=config,
        )
    assert not observer.observations


def test_executed_crushing_uses_each_crushers_yields(state: MarketState) -> None:
    """Count actual stock-limited inputs and aggregate differing configured yields.

    Args:
        state (MarketState): Ten thousand tonnes of regional soybeans.

    Returns:
        None: Confirms input/output pressure and next-month observation timing.
    """
    configs = {
        "a": CrusherConfig(
            monthly_capacity_tonnes=8000,
            processing_cost_usd_per_tonne=0,
            meal_yield_fraction=0.7,
            oil_yield_fraction=0.3,
        ),
        "b": CrusherConfig(
            monthly_capacity_tonnes=8000,
            processing_cost_usd_per_tonne=0,
            meal_yield_fraction=0.6,
            oil_yield_fraction=0.4,
        ),
    }
    agents = tuple(
        _RecordingAgent(
            AgentIdentity(
                agent_id=agent_id, agent_type=AgentType.CRUSHER, region=Region.BRAZIL
            ),
            ((DecisionAction.CRUSH, Commodity.SOYBEANS, 8000),),
        )
        for agent_id in configs
    )
    result = run_simulation(
        state,
        reversed(agents),
        crusher_configs=configs,
        months=2,
        monthly_flows=(),
        price_dynamics_config=PriceDynamicsConfig(),
    )
    first, second = result.monthly_results
    assert [item.executed_tonnes for item in first.settlement_results] == [8000, 2000]
    assert {
        pressure.commodity: (pressure.supply_tonnes, pressure.demand_tonnes)
        for pressure in first.market_pressures
    } == {
        Commodity.SOYBEANS: (0, 10000),
        Commodity.SOYBEAN_MEAL: (6800, 0),
        Commodity.SOYBEAN_OIL: (3200, 0),
    }
    assert {
        stock.commodity: stock.quantity_tonnes
        for stock in first.ending_state.snapshot.inventories
    } == {
        Commodity.SOYBEANS: 0,
        Commodity.SOYBEAN_MEAL: 6800,
        Commodity.SOYBEAN_OIL: 3200,
    }
    assert all(
        pressure.supply_tonnes == pressure.demand_tonnes == 0
        for pressure in second.market_pressures
    )
    for agent in agents:
        assert {
            price.commodity: price.price_usd_per_tonne
            for price in agent.observations[0].prices
        } == {
            Commodity.SOYBEANS: 250,
            Commodity.SOYBEAN_MEAL: 300,
            Commodity.SOYBEAN_OIL: 800,
        }
        assert {
            price.commodity: price.price_usd_per_tonne
            for price in agent.observations[1].prices
        } == {
            Commodity.SOYBEANS: 300,
            Commodity.SOYBEAN_MEAL: 240,
            Commodity.SOYBEAN_OIL: 640,
        }


def test_external_flows_count_only_physical_supply_and_consumption(
    state: MarketState,
) -> None:
    """Exclude reference meal/oil production from physical pressure and stocks.

    Args:
        state (MarketState): Initial market with global prices.

    Returns:
        None: Confirms exact external signals and single-month result boundaries.
    """
    initial = MarketState.from_snapshot(
        state.snapshot.model_copy(
            update={
                "inventories": tuple(
                    stock.model_copy(update={"quantity_tonnes": 100})
                    if stock.commodity != Commodity.SOYBEANS
                    else stock
                    for stock in state.snapshot.inventories
                )
            }
        )
    )
    flows = tuple(
        MarketFlow(
            month=state.snapshot.month,
            region=Region.BRAZIL,
            commodity=commodity,
            flow_type=flow_type,
            quantity_tonnes=quantity,
        )
        for commodity, flow_type, quantity in (
            (Commodity.SOYBEANS, MarketFlowType.SUPPLY, 100),
            (Commodity.SOYBEAN_MEAL, MarketFlowType.SUPPLY, 90000),
            (Commodity.SOYBEAN_OIL, MarketFlowType.SUPPLY, 80000),
            (Commodity.SOYBEAN_MEAL, MarketFlowType.CONSUMPTION, 30),
            (Commodity.SOYBEAN_OIL, MarketFlowType.CONSUMPTION, 20),
        )
    )
    result = run_simulation(
        initial,
        [],
        crusher_configs={},
        months=1,
        monthly_flows=reversed(flows),
        price_dynamics_config=PriceDynamicsConfig(),
    )
    assert len(result.monthly_results) == 1
    (monthly,) = result.monthly_results
    assert result.final_state.snapshot.month == state.snapshot.month
    assert {
        pressure.commodity: (pressure.supply_tonnes, pressure.demand_tonnes)
        for pressure in monthly.market_pressures
    } == {
        Commodity.SOYBEANS: (100, 0),
        Commodity.SOYBEAN_MEAL: (0, 30),
        Commodity.SOYBEAN_OIL: (0, 20),
    }
    assert {
        stock.commodity: stock.quantity_tonnes
        for stock in result.final_state.snapshot.inventories
    } == {
        Commodity.SOYBEANS: 10100,
        Commodity.SOYBEAN_MEAL: 70,
        Commodity.SOYBEAN_OIL: 80,
    }
    assert len(monthly.market_flows) == 5


def test_trades_use_executed_not_requested_quantities(two_regions: MarketState) -> None:
    """Count matched exports as source demand and imports as destination supply.

    Args:
        two_regions (MarketState): Brazilian stock and zero US inventories.

    Returns:
        None: Confirms exact bilateral signals while unmatched demand is excluded.
    """
    seller = _RecordingAgent(
        AgentIdentity(
            agent_id="seller", agent_type=AgentType.PRODUCER, region=Region.BRAZIL
        ),
        ((DecisionAction.OFFER_FOR_SALE, Commodity.SOYBEANS, 70),),
    )
    buyer = _RecordingAgent(
        AgentIdentity(
            agent_id="buyer", agent_type=AgentType.TRADER, region=Region.UNITED_STATES
        ),
        ((DecisionAction.PURCHASE, Commodity.SOYBEANS, 1000),),
    )
    result = run_simulation(
        two_regions,
        [seller, buyer],
        crusher_configs={},
        months=2,
        monthly_flows=(),
        price_dynamics_config=PriceDynamicsConfig(),
    )
    first, second = result.monthly_results
    assert [trade.quantity_tonnes for trade in first.executed_trades] == [70]
    assert {
        (item.region, item.commodity): (item.supply_tonnes, item.demand_tonnes)
        for item in first.market_pressures
        if item.commodity == Commodity.SOYBEANS
    } == {
        (Region.BRAZIL, Commodity.SOYBEANS): (0, 70),
        (Region.UNITED_STATES, Commodity.SOYBEANS): (70, 0),
    }
    assert {
        price.region: price.price_usd_per_tonne
        for price in second.regional_prices
        if price.commodity == Commodity.SOYBEANS
    } == {Region.BRAZIL: 300, Region.UNITED_STATES: 200}
    assert first.settlement_results == ()


def test_crushing_totals_use_stable_summation(state: MarketState) -> None:
    """Retain small crushing contributions alongside a much larger executed input.

    Args:
        state (MarketState): Source of synthetic price and inventory records.

    Returns:
        None: Confirms pressure uses stable sums for inputs and configured outputs.
    """
    initial = MarketState.from_snapshot(
        state.snapshot.model_copy(
            update={
                "inventories": tuple(
                    stock.model_copy(update={"quantity_tonnes": 1e16 + 4})
                    if stock.commodity == Commodity.SOYBEANS
                    else stock
                    for stock in state.snapshot.inventories
                )
            }
        )
    )
    quantities = (1e16, 1.0, 1.0)
    agents = tuple(
        _RecordingAgent(
            AgentIdentity(
                agent_id=str(index), agent_type=AgentType.CRUSHER, region=Region.BRAZIL
            ),
            ((DecisionAction.CRUSH, Commodity.SOYBEANS, quantity),),
        )
        for index, quantity in enumerate(quantities)
    )
    config = CrusherConfig(
        monthly_capacity_tonnes=1e16,
        processing_cost_usd_per_tonne=0,
        meal_yield_fraction=0.5,
        oil_yield_fraction=0.5,
    )
    result = run_simulation(
        initial,
        agents,
        crusher_configs={agent.identity.agent_id: config for agent in agents},
        months=1,
        monthly_flows=(),
        price_dynamics_config=PriceDynamicsConfig(),
    )
    pressures = {
        item.commodity: item for item in result.monthly_results[0].market_pressures
    }
    assert pressures[Commodity.SOYBEANS].demand_tonnes == math.fsum(quantities)
    assert pressures[Commodity.SOYBEAN_MEAL].supply_tonnes == math.fsum(
        quantity * 0.5 for quantity in quantities
    )


@pytest.mark.parametrize(
    ("first_month", "months"),
    [
        (date(2025, 1, 1), 1),
        (date(date.max.year, 12, 1), 1),
        (date(date.max.year, 11, 1), 2),
    ],
)
def test_final_month_records_pressure_without_calculating_unused_prices(
    state: MarketState, first_month: date, months: int
) -> None:
    """Finish dynamic runs at the calendar boundary without an extra price step.

    Args:
        state (MarketState): Synthetic inventory and global reference values.
        first_month (date): Normal month or near the maximum supported year.
        months (int): Requested history length.

    Returns:
        None: Confirms final pressure, current prices, and exactly needed updates.
    """
    initial = MarketState.from_snapshot(
        state.snapshot.model_copy(
            update={
                "month": first_month,
                "prices": tuple(
                    price.model_copy(update={"month": first_month})
                    for price in state.snapshot.prices
                ),
            }
        )
    )
    flows = tuple(
        MarketFlow(
            month=date(first_month.year, first_month.month + index, 1),
            region=Region.BRAZIL,
            commodity=Commodity.SOYBEANS,
            flow_type=MarketFlowType.SUPPLY,
            quantity_tonnes=100,
        )
        for index in range(months)
    )
    with patch(
        "soybean_sim.simulation._engine.update_regional_prices",
        wraps=update_regional_prices,
    ) as updater:
        result = run_simulation(
            initial,
            [],
            crusher_configs={},
            months=months,
            monthly_flows=flows,
            price_dynamics_config=PriceDynamicsConfig(),
        )
    assert updater.call_count == months - 1
    assert len(result.monthly_results) == months
    last = result.monthly_results[-1]
    assert len(last.market_pressures) == 3
    assert all(item.month == last.month for item in last.market_pressures)
    assert sum(item.supply_tonnes for item in last.market_pressures) == 100
    assert result.final_state == last.ending_state
    assert all(price.month == last.month for price in last.regional_prices)
    assert next(
        price.price_usd_per_tonne
        for price in last.regional_prices
        if price.commodity == Commodity.SOYBEANS
    ) == pytest.approx(250 * 0.8 ** (months - 1))
