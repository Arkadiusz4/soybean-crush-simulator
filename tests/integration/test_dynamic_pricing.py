from dataclasses import dataclass, field, replace
from datetime import date
from types import ModuleType

import pytest

from soybean_sim.agents import (
    AgentDecision,
    AgentIdentity,
    AgentType,
    Crusher,
    CrusherConfig,
)
from soybean_sim.market import (
    FlowSettlementError,
    MarketFlow,
    MarketFlowType,
    MarketState,
    PriceDynamicsConfig,
    RegionalPriceError,
    initialize_regional_prices,
)
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketSnapshot,
    Region,
)
from soybean_sim.simulation import SimulationError, SimulationResult, run_simulation


@dataclass
class _PriceObserver:
    """Capture exact regional views without adding synthetic market activity."""

    identity: AgentIdentity
    observations: list[MarketSnapshot] = field(default_factory=list)

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Record the view supplied by the engine and abstain.

        Args:
            snapshot (MarketSnapshot): Current regional prices and shared stocks.

        Returns:
            tuple[AgentDecision, ...]: Empty intentions.
        """
        self.observations.append(snapshot)
        return ()


def test_dynamic_prices_follow_actual_activity_across_year_boundary() -> None:
    """Connect physical flows, configured crushing, pressure, and next-month views.

    Returns:
        None: Confirms exact histories, immutable inputs, and deterministic feedback.
    """
    december, january = date(2023, 12, 1), date(2024, 1, 1)
    global_prices = {
        Commodity.SOYBEANS: 250,
        Commodity.SOYBEAN_MEAL: 300,
        Commodity.SOYBEAN_OIL: 800,
    }
    regions = (Region.BRAZIL, Region.EUROPEAN_UNION)
    initial = MarketState.from_snapshot(
        MarketSnapshot(
            month=december,
            prices=tuple(
                CommodityPrice(
                    commodity=commodity,
                    month=december,
                    price_usd_per_tonne=value,
                    data_source="synthetic dynamic integration",
                )
                for commodity, value in global_prices.items()
            ),
            inventories=tuple(
                Inventory(
                    region=region,
                    commodity=commodity,
                    quantity_tonnes=1000 if commodity == Commodity.SOYBEANS else 100,
                )
                for region in regions
                for commodity in sorted(Commodity)
            ),
        )
    )
    baselines = tuple(
        price.model_copy(update={"price_usd_per_tonne": price.price_usd_per_tonne * 2})
        if price.region == Region.EUROPEAN_UNION
        else price
        for price in initialize_regional_prices(initial.snapshot, regions)
    )
    config = CrusherConfig(
        monthly_capacity_tonnes=100, processing_cost_usd_per_tonne=10
    )
    crusher = Crusher(
        AgentIdentity(
            agent_id="crusher-br", agent_type=AgentType.CRUSHER, region=Region.BRAZIL
        ),
        config,
    )
    observers = tuple(
        _PriceObserver(
            AgentIdentity(
                agent_id=f"observer-{region}",
                agent_type=AgentType.TRADER,
                region=region,
            )
        )
        for region in regions
    )
    agents = (crusher, *observers)
    flows = tuple(
        MarketFlow(
            month=month,
            region=Region.BRAZIL,
            commodity=commodity,
            flow_type=flow_type,
            quantity_tonnes=quantity,
        )
        for month in (december, january)
        for commodity, flow_type, quantity in (
            (Commodity.SOYBEANS, MarketFlowType.SUPPLY, 200),
            (Commodity.SOYBEAN_MEAL, MarketFlowType.SUPPLY, 90000),
            (Commodity.SOYBEAN_MEAL, MarketFlowType.CONSUMPTION, 40),
            (Commodity.SOYBEAN_OIL, MarketFlowType.CONSUMPTION, 10),
        )
    )
    dynamics = PriceDynamicsConfig()
    original = (
        initial.model_dump_json(),
        tuple(flow.model_dump_json() for flow in flows),
        tuple(price.model_dump_json() for price in baselines),
        config.model_dump_json(),
        dynamics.model_dump_json(),
    )
    result = run_simulation(
        initial,
        agents,
        crusher_configs={"crusher-br": config},
        months=2,
        monthly_flows=iter(flows),
        regional_prices=iter(baselines),
        price_dynamics_config=dynamics,
    )
    assert [monthly.month for monthly in result.monthly_results] == [december, january]
    for observer in observers:
        expected_values = (
            (
                global_prices,
                {
                    Commodity.SOYBEANS: 225,
                    Commodity.SOYBEAN_MEAL: 270,
                    Commodity.SOYBEAN_OIL: 720,
                },
            )
            if observer.identity.region == Region.BRAZIL
            else ({commodity: value * 2 for commodity, value in global_prices.items()},)
            * 2
        )
        assert [snapshot.month for snapshot in observer.observations] == [
            december,
            january,
        ]
        for snapshot, expected in zip(
            observer.observations, expected_values, strict=True
        ):
            assert {
                price.commodity: price.price_usd_per_tonne for price in snapshot.prices
            } == pytest.approx(expected)
            assert all(price.month == snapshot.month for price in snapshot.prices)
    for monthly in result.monthly_results:
        assert len(monthly.regional_prices) == len(monthly.market_pressures) == 6
        assert all(price.month == monthly.month for price in monthly.regional_prices)
        assert all(
            pressure.month == monthly.month for pressure in monthly.market_pressures
        )
        assert [settled.executed_tonnes for settled in monthly.settlement_results] == [
            100
        ]
        assert {
            pressure.commodity: (pressure.supply_tonnes, pressure.demand_tonnes)
            for pressure in monthly.market_pressures
            if pressure.region == Region.BRAZIL
        } == {
            Commodity.SOYBEANS: (200, 100),
            Commodity.SOYBEAN_MEAL: (80, 40),
            Commodity.SOYBEAN_OIL: (20, 10),
        }
        assert all(
            pressure.supply_tonnes == pressure.demand_tonnes == 0
            for pressure in monthly.market_pressures
            if pressure.region == Region.EUROPEAN_UNION
        )
        for observer in observers:
            assert {
                price.commodity: price.price_usd_per_tonne
                for price in monthly.regional_prices
                if price.region == observer.identity.region
            } == {
                price.commodity: price.price_usd_per_tonne
                for snapshot in observer.observations
                if snapshot.month == monthly.month
                for price in snapshot.prices
            }
        for state in (monthly.starting_state, monthly.ending_state):
            assert {
                price.commodity: price.price_usd_per_tonne
                for price in state.snapshot.prices
            } == global_prices
    assert {
        stock.commodity: stock.quantity_tonnes
        for stock in result.final_state.snapshot.inventories
        if stock.region == Region.BRAZIL
    } == {
        Commodity.SOYBEANS: 1200,
        Commodity.SOYBEAN_MEAL: 180,
        Commodity.SOYBEAN_OIL: 120,
    }
    assert (
        result.monthly_results[1].starting_state.snapshot.inventories
        == result.monthly_results[0].ending_state.snapshot.inventories
    )
    assert result.initial_state == initial
    assert result.final_state == result.monthly_results[-1].ending_state
    assert SimulationResult.model_validate_json(result.model_dump_json()) == result
    assert original == (
        initial.model_dump_json(),
        tuple(flow.model_dump_json() for flow in flows),
        tuple(price.model_dump_json() for price in baselines),
        config.model_dump_json(),
        dynamics.model_dump_json(),
    )
    assert result == run_simulation(
        initial,
        reversed(agents),
        crusher_configs={"crusher-br": config},
        months=2,
        monthly_flows=reversed(flows),
        regional_prices=reversed(baselines),
        price_dynamics_config=dynamics,
    )


def test_three_month_market_feedback_is_deterministic_and_immutable(
    market_smoke: ModuleType,
) -> None:
    """Validate independently calculated pressure/prices and all recorded agent views.

    Args:
        market_smoke (ModuleType): Shared synthetic scenario and acceptance checks.

    Returns:
        None: Confirms actual trade feedback, next-month timing, and immutable inputs.
    """
    case = market_smoke.create_market()
    original = market_smoke.fingerprint_inputs(case)
    result = market_smoke.run_market(case)
    market_smoke.check_history(case, result)
    market_smoke.check_pressures(result)
    market_smoke.check_prices(result)
    assert result == market_smoke.run_market(case)
    for seed in (0, 17, 42):
        assert result == market_smoke.run_market(case, shuffle_seed=seed)
        market_smoke.check_history(case, result)
    assert market_smoke.fingerprint_inputs(case) == original
    assert SimulationResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("price_floor", [0.01, 400.0])
def test_zero_activity_and_minimum_price_floor_policy(
    market_smoke: ModuleType, price_floor: float
) -> None:
    """Keep zero pressure independent of stocks; let the price floor override the cap.

    Args:
        market_smoke (ModuleType): Two-region initial state with unequal stocks.
        price_floor (float): Default floor or an explicit floor above initial prices.

    Returns:
        None: Confirms no physical activity and independently expected price values.
    """
    case = market_smoke.create_market()
    result = run_simulation(
        case.state,
        [],
        crusher_configs={},
        months=3,
        monthly_flows=(),
        price_dynamics_config=PriceDynamicsConfig(
            minimum_price_usd_per_tonne=price_floor
        ),
    )
    market_smoke.check_accounting(result, {})
    for index, monthly in enumerate(result.monthly_results):
        assert (
            monthly.decisions
            == monthly.settlement_results
            == monthly.executed_trades
            == monthly.market_flows
            == ()
        )
        assert all(
            item.supply_tonnes == item.demand_tonnes == 0
            for item in monthly.market_pressures
        )
        assert (
            monthly.ending_state.snapshot.inventories == case.state.snapshot.inventories
        )
        expected = {
            Commodity.SOYBEANS: 250,
            Commodity.SOYBEAN_MEAL: 300,
            Commodity.SOYBEAN_OIL: 800,
        }
        if index and price_floor == 400:
            expected = {
                Commodity.SOYBEANS: 400,
                Commodity.SOYBEAN_MEAL: 400,
                Commodity.SOYBEAN_OIL: 800,
            }
        assert all(
            price.price_usd_per_tonne == expected[price.commodity]
            for price in monthly.regional_prices
        )


def test_multiple_stock_limited_crushers_preserve_pressure_and_accounting(
    market_smoke: ModuleType,
) -> None:
    """Aggregate distinct yield settings using executed inputs rather than requests.

    Args:
        market_smoke (ModuleType): Shared stock accounting assertion and base market.

    Returns:
        None: Confirms partial execution, configured outputs, and inactive next month.
    """
    case = market_smoke.create_market()
    state = MarketState.from_snapshot(
        case.state.snapshot.model_copy(
            update={
                "inventories": tuple(
                    stock.model_copy(update={"quantity_tonnes": 150})
                    if (stock.region, stock.commodity)
                    == (Region.BRAZIL, Commodity.SOYBEANS)
                    else stock
                    for stock in case.state.snapshot.inventories
                )
            }
        )
    )
    crushers = tuple(
        Crusher(
            AgentIdentity(
                agent_id=agent_id, agent_type=AgentType.CRUSHER, region=Region.BRAZIL
            ),
            CrusherConfig(
                monthly_capacity_tonnes=120,
                processing_cost_usd_per_tonne=10,
                meal_yield_fraction=meal_yield,
                oil_yield_fraction=oil_yield,
            ),
        )
        for agent_id, meal_yield, oil_yield in (("a", 0.7, 0.3), ("b", 0.6, 0.4))
    )
    configs = {crusher.identity.agent_id: crusher.config for crusher in crushers}
    result = run_simulation(
        state,
        reversed(crushers),
        crusher_configs=configs,
        months=2,
        monthly_flows=(),
        price_dynamics_config=PriceDynamicsConfig(),
    )
    first, second = result.monthly_results
    assert sum(item.quantity_tonnes for item in first.decisions) == 240
    assert [item.executed_tonnes for item in first.settlement_results] == [120, 30]
    assert {
        item.commodity: (item.supply_tonnes, item.demand_tonnes)
        for item in first.market_pressures
        if item.region == Region.BRAZIL
    } == {
        Commodity.SOYBEANS: (0, 150),
        Commodity.SOYBEAN_MEAL: (102, 0),
        Commodity.SOYBEAN_OIL: (48, 0),
    }
    assert second.decisions == second.settlement_results == ()
    assert all(
        item.supply_tonnes == item.demand_tonnes == 0
        for item in second.market_pressures
    )
    market_smoke.check_accounting(result, configs)


@pytest.mark.parametrize(
    ("defect", "error", "message"),
    [
        ("no_flows", SimulationError, "requires monthly_flows"),
        ("coverage", RegionalPriceError, "coverage"),
        ("duplicate", SimulationError, "Duplicate monthly flow"),
        ("invalid_flow", SimulationError, "Invalid monthly flow"),
        ("shortage", FlowSettlementError, "Insufficient inventory"),
        ("missing_crusher", SimulationError, "Missing processing configuration"),
        ("mismatched_crusher", SimulationError, "does not match"),
    ],
)
def test_invalid_complete_markets_fail_without_mutating_inputs(
    market_smoke: ModuleType, defect: str, error: type[ValueError], message: str
) -> None:
    """Exercise validation and shortage failures with all real agent roles connected.

    Args:
        market_smoke (ModuleType): Original three-month market inputs.
        defect (str): Deliberate invalid input or physical stock shortage.
        error (type[ValueError]): Existing domain-specific exception.
        message (str): Useful error context expected from the failing stage.

    Returns:
        None: Confirms failure propagation without changing caller-owned inputs.
    """
    case = market_smoke.create_market()
    original = market_smoke.fingerprint_inputs(case)
    flows, prices, configs = (
        case.flows,
        case.regional_prices,
        dict(case.crusher_configs),
    )
    if defect == "no_flows":
        flows = None
    elif defect == "coverage":
        prices = prices[:-1]
    elif defect == "duplicate":
        flows = (*flows, flows[0])
    elif defect == "invalid_flow":
        flows = (flows[0].model_copy(update={"quantity_tonnes": -1}), *flows[1:])
    elif defect == "shortage":
        flows = tuple(
            flow.model_copy(update={"quantity_tonnes": 200})
            if (flow.month, flow.region, flow.commodity, flow.flow_type)
            == (
                case.months[0],
                Region.EUROPEAN_UNION,
                Commodity.SOYBEAN_MEAL,
                MarketFlowType.CONSUMPTION,
            )
            else flow
            for flow in flows
        )
    elif defect == "missing_crusher":
        configs = {}
    else:
        configs = {
            agent_id: config.model_copy(update={"monthly_capacity_tonnes": 200})
            for agent_id, config in configs.items()
        }
    with pytest.raises(error, match=message):
        run_simulation(
            case.state,
            case.agents,
            crusher_configs=configs,
            months=3,
            monthly_flows=flows,
            regional_prices=prices,
            price_dynamics_config=case.price_config,
        )
    assert market_smoke.fingerprint_inputs(case) == original


def test_smoke_check_reports_failure_with_nonzero_status(
    market_smoke: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ensure a failed invariant makes the executable smoke check report failure.

    Args:
        market_smoke (ModuleType): Shared smoke entry point.
        monkeypatch (pytest.MonkeyPatch): Temporarily replace setup with invalid flows.
        capsys (pytest.CaptureFixture[str]): Captured report output.

    Returns:
        None: Confirms failure status and descriptive error output.
    """
    case = market_smoke.create_market()
    invalid = replace(case, flows=(case.flows[0], case.flows[0]))
    monkeypatch.setattr(market_smoke, "create_market", lambda: invalid)
    assert market_smoke.main([]) == 1
    assert "Duplicate monthly flow" in capsys.readouterr().err
