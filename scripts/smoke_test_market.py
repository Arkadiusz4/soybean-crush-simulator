"""Validate a synthetic physical market with dynamic prices; no datasets required."""

import argparse
import math
import random
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final

import market_smoke_report as report
from pydantic import ValidationError
from rich.console import Console

from soybean_sim.agents import (
    Agent,
    AgentDecision,
    AgentIdentity,
    AgentType,
    Crusher,
    CrusherConfig,
    DecisionAction,
    Producer,
    ProducerConfig,
    Trader,
    TraderConfig,
)
from soybean_sim.market import (
    FlowSettlementError,
    MarketFlow,
    MarketFlowError,
    MarketFlowType,
    MarketPressureError,
    MarketState,
    MonthlyFlowConfig,
    PriceDynamicsConfig,
    PriceDynamicsError,
    RegionalPrice,
    RegionalPriceError,
    SettlementError,
    TradeMatchingError,
    TradeSettlementError,
    generate_monthly_flows,
    initialize_regional_prices,
)
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketBalance,
    MarketSnapshot,
    Region,
)
from soybean_sim.simulation import SimulationError, SimulationResult, run_simulation

_MONTHS: Final = tuple(date(2025, month, 1) for month in (1, 2, 3))
_REGIONS: Final = (Region.BRAZIL, Region.EUROPEAN_UNION)
_REL_TOLERANCE: Final = 1e-10
_ABS_TOLERANCE: Final = 1e-8


@dataclass
class ObservedAgent:
    """Delegate to a concrete agent while recording its exact monthly views."""

    agent: Agent
    observations: list[MarketSnapshot] = field(default_factory=list)

    @property
    def identity(self) -> AgentIdentity:
        """Expose the wrapped registration.

        Returns:
            AgentIdentity: Concrete agent's unchanged identity.
        """
        return self.agent.identity

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Record the observation and forward the concrete decision rule.

        Args:
            snapshot (MarketSnapshot): Current regional prices and shared stocks.

        Returns:
            tuple[AgentDecision, ...]: Unmodified intentions.
        """
        self.observations.append(snapshot)
        return self.agent.decide(snapshot)


@dataclass(frozen=True)
class SyntheticMarket:
    """Keep shared smoke-test inputs together without changing production APIs."""

    state: MarketState
    agents: tuple[Agent, ...]
    crusher_configs: Mapping[str, CrusherConfig]
    flows: tuple[MarketFlow, ...]
    regional_prices: tuple[RegionalPrice, ...]
    price_config: PriceDynamicsConfig
    months: tuple[date, ...] = _MONTHS


def create_market() -> SyntheticMarket:
    """Build two regions with real agents and deliberately matched soybean demand.

    Returns:
        SyntheticMarket: Three months of illustrative inputs, not historical data.
    """
    state = _create_state()
    agents, configs = _create_agents()
    return SyntheticMarket(
        state,
        agents,
        configs,
        _create_flows(),
        initialize_regional_prices(state.snapshot, _REGIONS),
        PriceDynamicsConfig(),
    )


def _create_state() -> MarketState:
    """Provide sufficient synthetic starting stocks and global reference values.

    Returns:
        MarketState: January observations with explicit stocks for every market.
    """
    return MarketState.from_snapshot(
        MarketSnapshot(
            month=_MONTHS[0],
            prices=tuple(
                CommodityPrice(
                    commodity=commodity,
                    month=_MONTHS[0],
                    price_usd_per_tonne=price,
                    data_source="synthetic Milestone 3 reference",
                )
                for commodity, price in (
                    (Commodity.SOYBEANS, 250),
                    (Commodity.SOYBEAN_MEAL, 300),
                    (Commodity.SOYBEAN_OIL, 800),
                )
            ),
            inventories=tuple(
                Inventory(
                    region=region,
                    commodity=commodity,
                    quantity_tonnes=1000
                    if (region, commodity) == (Region.BRAZIL, Commodity.SOYBEANS)
                    else 100,
                )
                for region in _REGIONS
                for commodity in sorted(Commodity)
            ),
        )
    )


def _create_agents() -> tuple[tuple[Agent, ...], dict[str, CrusherConfig]]:
    """Configure real strategies so Brazilian offers meet European soybean demand.

    Returns:
        tuple: Agents and the exact processing configuration also used by settlement.
    """
    producer = Producer(
        AgentIdentity(
            agent_id="producer-br", agent_type=AgentType.PRODUCER, region=Region.BRAZIL
        ),
        ProducerConfig(reference_price_usd_per_tonne=250),
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
    trader = Trader(
        AgentIdentity(
            agent_id="trader-eu",
            agent_type=AgentType.TRADER,
            region=Region.EUROPEAN_UNION,
        ),
        TraderConfig(
            reference_soybean_price_usd_per_tonne=250,
            reference_soybean_meal_price_usd_per_tonne=300,
            reference_soybean_oil_price_usd_per_tonne=800,
            maximum_monthly_purchase_tonnes=50,
        ),
    )
    return (
        (ObservedAgent(producer), crusher, ObservedAgent(trader)),
        {crusher.identity.agent_id: config},
    )


def _create_flows() -> tuple[MarketFlow, ...]:
    """Allocate explicitly synthetic annual figures with existing uniform weights.

    Returns:
        tuple[MarketFlow, ...]: Physical supply/consumption and reference supply.
    """
    balances = tuple(
        MarketBalance(
            region=region,
            commodity=commodity,
            marketing_year="synthetic",
            production_tonnes=production,
            domestic_consumption_tonnes=consumption,
            imports_tonnes=None,
            exports_tonnes=None,
            beginning_stocks_tonnes=None,
            ending_stocks_tonnes=None,
        )
        for region, commodity, production, consumption in (
            (Region.BRAZIL, Commodity.SOYBEANS, 2400, 1200),
            (Region.BRAZIL, Commodity.SOYBEAN_MEAL, 10800, 480),
            (Region.BRAZIL, Commodity.SOYBEAN_OIL, 12000, 120),
            (Region.EUROPEAN_UNION, Commodity.SOYBEAN_MEAL, None, 240),
            (Region.EUROPEAN_UNION, Commodity.SOYBEAN_OIL, None, 120),
        )
    )
    return tuple(
        flow
        for month in _MONTHS
        for flow in generate_monthly_flows(
            balances,
            month=month,
            config=MonthlyFlowConfig(),
        )
    )


def run_market(
    case: SyntheticMarket, *, shuffle_seed: int | None = None
) -> SimulationResult:
    """Execute physical mode, optionally shuffling local copies of input collections.

    Args:
        case (SyntheticMarket): Validated synthetic scenario inputs.
        shuffle_seed (int | None): Seed for input ordering, not agent behavior.

    Returns:
        SimulationResult: Complete physical and dynamic monthly history.
    """
    agents, prices, flows = (
        list(case.agents),
        list(case.regional_prices),
        list(case.flows),
    )
    if shuffle_seed is not None:
        ordering = random.Random(shuffle_seed)
        for records in (agents, prices, flows):
            ordering.shuffle(records)
    return run_simulation(
        case.state,
        agents,
        crusher_configs=case.crusher_configs,
        months=len(case.months),
        regional_prices=prices,
        monthly_flows=flows,
        price_dynamics_config=case.price_config,
    )


def check_accounting(
    result: SimulationResult, configs: Mapping[str, CrusherConfig]
) -> None:
    """Check regional stock identities independently of settlement helpers.

    Args:
        result (SimulationResult): Completed physical history.
        configs (Mapping[str, CrusherConfig]): Yields used for executed crushing.

    Returns:
        None: Assertions verify finite stocks and all physical additions/removals.
    """
    for monthly in result.monthly_results:
        changes = {
            (stock.region, stock.commodity): []
            for stock in monthly.starting_state.snapshot.inventories
        }
        for flow in monthly.market_flows:
            if (
                flow.flow_type == MarketFlowType.SUPPLY
                and flow.commodity == Commodity.SOYBEANS
            ):
                changes[flow.region, flow.commodity].append(flow.quantity_tonnes)
            elif flow.flow_type == MarketFlowType.CONSUMPTION:
                changes[flow.region, flow.commodity].append(-flow.quantity_tonnes)
        for settled in monthly.settlement_results:
            if settled.decision.action != DecisionAction.CRUSH:
                continue
            region = settled.decision.agent.region
            config = configs[settled.decision.agent.agent_id]
            changes[region, Commodity.SOYBEANS].append(-settled.executed_tonnes)
            changes[region, Commodity.SOYBEAN_MEAL].append(
                settled.executed_tonnes * config.meal_yield_fraction
            )
            changes[region, Commodity.SOYBEAN_OIL].append(
                settled.executed_tonnes * config.oil_yield_fraction
            )
        for trade in monthly.executed_trades:
            changes[trade.source_region, trade.commodity].append(-trade.quantity_tonnes)
            changes[trade.destination_region, trade.commodity].append(
                trade.quantity_tonnes
            )
        opening = {
            (stock.region, stock.commodity): stock.quantity_tonnes
            for stock in monthly.starting_state.snapshot.inventories
        }
        for stock in monthly.ending_state.snapshot.inventories:
            key = (stock.region, stock.commodity)
            expected = math.fsum([opening[key], *changes[key]])
            assert (
                math.isfinite(stock.quantity_tonnes) and stock.quantity_tonnes >= 0
            ), key
            assert math.isclose(
                stock.quantity_tonnes,
                expected,
                rel_tol=_REL_TOLERANCE,
                abs_tol=_ABS_TOLERANCE,
            ), (monthly.month, key, expected)


def fingerprint_inputs(case: SyntheticMarket) -> tuple[str, ...]:
    """Serialize inputs for mutation checks while excluding observation logs.

    Args:
        case (SyntheticMarket): Original state, records, and concrete configurations.

    Returns:
        tuple[str, ...]: Stable model representations in their original input order.
    """
    agent_configs = []
    for agent in case.agents:
        concrete = agent.agent if isinstance(agent, ObservedAgent) else agent
        if isinstance(concrete, (Producer, Crusher, Trader)):
            agent_configs.append(concrete.config.model_dump_json())
    return (
        case.state.model_dump_json(),
        case.price_config.model_dump_json(),
        *(flow.model_dump_json() for flow in case.flows),
        *(price.model_dump_json() for price in case.regional_prices),
        *(
            config.model_dump_json()
            for _, config in sorted(case.crusher_configs.items())
        ),
        *agent_configs,
    )


def check_pressures(result: SimulationResult) -> None:
    """Check explicit synthetic activity totals, excluding unmatched product offers.

    Args:
        result (SimulationResult): Three-month synthetic run.

    Returns:
        None: Assertions verify physical signals without calling pressure calculation.
    """
    for monthly, traded in zip(result.monthly_results, (25, 50, 50), strict=True):
        assert {
            (item.region, item.commodity): (item.supply_tonnes, item.demand_tonnes)
            for item in monthly.market_pressures
        } == {
            (Region.BRAZIL, Commodity.SOYBEANS): (200, 100 + traded),
            (Region.BRAZIL, Commodity.SOYBEAN_MEAL): (80, 40),
            (Region.BRAZIL, Commodity.SOYBEAN_OIL): (20, 10),
            (Region.EUROPEAN_UNION, Commodity.SOYBEANS): (traded, 0),
            (Region.EUROPEAN_UNION, Commodity.SOYBEAN_MEAL): (0, 20),
            (Region.EUROPEAN_UNION, Commodity.SOYBEAN_OIL): (0, 10),
        }, monthly.month
        assert len(monthly.executed_trades) == 1
        trade = monthly.executed_trades[0]
        assert (
            trade.source_region,
            trade.destination_region,
            trade.commodity,
            trade.quantity_tonnes,
        ) == (Region.BRAZIL, Region.EUROPEAN_UNION, Commodity.SOYBEANS, traded)


def check_prices(result: SimulationResult) -> None:
    """Compare independently worked price trajectories and the configured change cap.

    Args:
        result (SimulationResult): Synthetic history using default dynamics settings.

    Returns:
        None: Assertions verify direction, magnitude, and current-month dating.
    """
    expected = {
        (Region.BRAZIL, Commodity.SOYBEANS): (250, 231.25, 219.6875),
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL): (300, 270, 243),
        (Region.BRAZIL, Commodity.SOYBEAN_OIL): (800, 720, 648),
        (Region.EUROPEAN_UNION, Commodity.SOYBEANS): (250, 200, 160),
        (Region.EUROPEAN_UNION, Commodity.SOYBEAN_MEAL): (300, 360, 432),
        (Region.EUROPEAN_UNION, Commodity.SOYBEAN_OIL): (800, 960, 1152),
    }
    for index, monthly in enumerate(result.monthly_results):
        assert len(monthly.regional_prices) == len(expected)
        for price in monthly.regional_prices:
            values = expected[price.region, price.commodity]
            assert price.month == monthly.month
            assert math.isclose(
                price.price_usd_per_tonne,
                values[index],
                rel_tol=_REL_TOLERANCE,
                abs_tol=_ABS_TOLERANCE,
            )
            if index:
                assert (
                    abs(price.price_usd_per_tonne / values[index - 1] - 1)
                    <= 0.2 + _REL_TOLERANCE
                )


def check_history(case: SyntheticMarket, result: SimulationResult) -> None:
    """Verify calendar, carryover, reference prices, and logged regional observations.

    Args:
        case (SyntheticMarket): Original scenario and observation logs.
        result (SimulationResult): Completed synthetic history.

    Returns:
        None: Assertions verify no missing months or same-month price look-ahead.
    """
    assert tuple(monthly.month for monthly in result.monthly_results) == case.months
    assert result.initial_state == case.state
    assert result.final_state == result.monthly_results[-1].ending_state
    global_values = {
        price.commodity: price.price_usd_per_tonne
        for price in case.state.snapshot.prices
    }
    for index, monthly in enumerate(result.monthly_results):
        assert monthly.physical_cycle
        if index:
            assert (
                monthly.starting_state.snapshot.inventories
                == result.monthly_results[index - 1].ending_state.snapshot.inventories
            )
        for state in (monthly.starting_state, monthly.ending_state):
            assert {
                price.commodity: price.price_usd_per_tonne
                for price in state.snapshot.prices
            } == global_values
            assert all(price.month == monthly.month for price in state.snapshot.prices)
        assert all(
            item.month == monthly.month
            for item in (
                *monthly.market_pressures,
                *monthly.executed_trades,
                *monthly.market_flows,
            )
        )
    for agent in case.agents:
        if not isinstance(agent, ObservedAgent):
            continue
        observed = agent.observations[-len(case.months) :]
        assert tuple(snapshot.month for snapshot in observed) == case.months
        for monthly, snapshot in zip(result.monthly_results, observed, strict=True):
            assert {
                price.commodity: price.price_usd_per_tonne for price in snapshot.prices
            } == {
                price.commodity: price.price_usd_per_tonne
                for price in monthly.regional_prices
                if price.region == agent.identity.region
            }


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the synthetic market and render a concise or detailed terminal report.

    Args:
        argv (Sequence[str] | None): Arguments to parse; None reads the command line.

    Returns:
        int: Zero on success; one for an assertion or domain validation failure.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose", action="store_true", help="Show monthly regional market pressures."
    )
    options = parser.parse_args(argv)
    completed: list[str] = []
    result: SimulationResult | None = None
    agent_count = 0
    stage = "Synthetic market setup"
    try:
        case = create_market()
        agent_count = len(case.agents)
        original_inputs = fingerprint_inputs(case)
        stage = "Simulation execution"
        result = run_market(case)
        stage = "History and carryover"
        check_history(case, result)
        completed.append(stage)
        stage = "Physical inventory accounting"
        check_accounting(result, case.crusher_configs)
        completed.append(stage)
        stage = "Executed interregional trades"
        assert any(monthly.executed_trades for monthly in result.monthly_results), (
            "No interregional trades executed"
        )
        completed.append(stage)
        stage = "Market pressure calculations"
        check_pressures(result)
        completed.append(stage)
        stage = "Dynamic regional pricing"
        check_prices(result)
        completed.append(stage)
        stage = "Determinism"
        assert result == run_market(case), "Repeated execution differs"
        assert result == run_market(case, shuffle_seed=42), (
            "Shuffled inputs change results"
        )
        completed.append(stage)
        stage = "Input immutability"
        assert fingerprint_inputs(case) == original_inputs, "Simulation mutated inputs"
        completed.append(stage)
    except (
        AssertionError,
        ValidationError,
        SimulationError,
        FlowSettlementError,
        MarketFlowError,
        MarketPressureError,
        PriceDynamicsError,
        RegionalPriceError,
        SettlementError,
        TradeMatchingError,
        TradeSettlementError,
        OverflowError,
    ) as exc:
        console = Console(stderr=True)
        report.print_header(console, result, agent_count=agent_count)
        report.print_validation_summary(
            console, completed, failed_stage=stage, error=exc
        )
        return 1
    report.print_report(
        Console(),
        result,
        agent_count=len(case.agents),
        completed=completed,
        verbose=options.verbose,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
