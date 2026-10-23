"""
Run with ``uv run python scripts/smoke_test_simulation.py`` from the repository.
Capacities and costs are illustrative settings, not estimates from historical data.
USDA beginning stocks are used without adjusting them to the calendar price month.
"""

import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Final

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
from soybean_sim.data import build_simulation_input
from soybean_sim.data.usda import load_usda_psd_csv
from soybean_sim.data.world_bank import load_world_bank_prices
from soybean_sim.market import MarketState, SettlementOutcome
from soybean_sim.models import Commodity, MarketSnapshot, Region, SimulationInput
from soybean_sim.simulation import MonthlyResult, SimulationResult, run_simulation

_REPOSITORY: Final = Path(__file__).resolve().parents[1]
_USDA_PATH: Final = _REPOSITORY / "data/raw/psd_oilseeds.csv"
_WORLD_BANK_PATH: Final = _REPOSITORY / "data/raw/CMO-Historical-Data-Monthly.xlsx"
_MARKETING_YEAR: Final = "2024"
_PRICE_MONTH: Final = date(2025, 1, 1)
_MONTHS: Final = tuple(date(2025, month, 1) for month in range(1, 13))
_REL_TOLERANCE: Final = 1e-10
_ABS_TOLERANCE_TONNES: Final = 1e-6


@dataclass
class _ObservedAgent:
    """Forward decisions unchanged while recording even months of abstention."""

    agent: Agent
    months: list[date] = field(default_factory=list)

    @property
    def identity(self) -> AgentIdentity:
        """Expose the concrete agent's original registration identity."""
        return self.agent.identity

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Record the actual invocation before delegating to the concrete agent."""
        self.months.append(snapshot.month)
        return self.agent.decide(snapshot)


def _load_input() -> SimulationInput:
    """Select all supported annual balances and January global reference prices."""
    balances = load_usda_psd_csv(_USDA_PATH, marketing_year=_MARKETING_YEAR)
    prices = load_world_bank_prices(
        _WORLD_BANK_PATH, start_month=_PRICE_MONTH, end_month=_PRICE_MONTH
    )
    return build_simulation_input(
        balances,
        prices,
        marketing_year=_MARKETING_YEAR,
        price_month=_PRICE_MONTH,
        regions=tuple(Region),
        commodities=tuple(Commodity),
    )


def _create_agents(
    initial: SimulationInput,
) -> tuple[tuple[Agent, ...], dict[str, CrusherConfig]]:
    """Build one agent of each role per region, using initial prices as baselines."""
    prices = {item.commodity: item.price_usd_per_tonne for item in initial.prices}
    agents: list[Agent] = []
    configs: dict[str, CrusherConfig] = {}
    for region in sorted(Region):
        producer = Producer(
            identity=AgentIdentity(
                agent_id=f"producer-{region.value}",
                agent_type=AgentType.PRODUCER,
                region=region,
            ),
            config=ProducerConfig(
                reference_price_usd_per_tonne=prices[Commodity.SOYBEANS]
            ),
        )
        crusher = Crusher(
            identity=AgentIdentity(
                agent_id=f"crusher-{region.value}",
                agent_type=AgentType.CRUSHER,
                region=region,
            ),
            config=CrusherConfig(
                monthly_capacity_tonnes=10000,
                processing_cost_usd_per_tonne=35,
                meal_yield_fraction=0.80,
                oil_yield_fraction=0.20,
            ),
        )
        trader = Trader(
            identity=AgentIdentity(
                agent_id=f"trader-{region.value}",
                agent_type=AgentType.TRADER,
                region=region,
            ),
            config=TraderConfig(
                reference_soybean_price_usd_per_tonne=prices[Commodity.SOYBEANS],
                reference_soybean_meal_price_usd_per_tonne=prices[
                    Commodity.SOYBEAN_MEAL
                ],
                reference_soybean_oil_price_usd_per_tonne=prices[Commodity.SOYBEAN_OIL],
                maximum_monthly_purchase_tonnes=5000,
            ),
        )
        agents.extend((producer, crusher, trader))
        configs[crusher.identity.agent_id] = crusher.config
    return tuple(agents), configs


def _assert_close(actual: float, expected: float, context: str) -> None:
    """Compare tonnes with tolerance for accumulated arithmetic on large stocks."""
    assert math.isclose(
        actual,
        expected,
        rel_tol=_REL_TOLERANCE,
        abs_tol=_ABS_TOLERANCE_TONNES,
    ), f"{context}: expected {expected}, got {actual}"


def _validate_settlement(
    monthly: MonthlyResult, configs: dict[str, CrusherConfig]
) -> None:
    """Check decision coverage, deferred trades, sequential stock bounds and yields."""
    assert Counter(monthly.decisions) == Counter(
        item.decision for item in monthly.settlement_results
    ), f"{monthly.month}: settlements must cover every decision exactly once"
    expected_stocks = {
        (item.region, item.commodity): item.quantity_tonnes
        for item in monthly.starting_state.snapshot.inventories
    }
    for result in monthly.settlement_results:
        decision = result.decision
        context = f"{monthly.month}/{decision.agent.agent_id}"
        _assert_close(result.requested_tonnes, decision.quantity_tonnes, context)
        assert result.executed_tonnes <= result.requested_tonnes, context
        if decision.action != DecisionAction.CRUSH:
            assert result.executed_tonnes == 0, context
            assert result.unfulfilled_tonnes == result.requested_tonnes, context
            assert result.outcome == SettlementOutcome.TRADE_MATCHING_NOT_IMPLEMENTED
            continue

        assert result.outcome == SettlementOutcome.CRUSH_SETTLED, context
        region = decision.agent.region
        soybean_key = (region, Commodity.SOYBEANS)
        assert result.executed_tonnes <= expected_stocks[soybean_key], context
        config = configs[decision.agent.agent_id]
        expected_stocks[soybean_key] -= result.executed_tonnes
        expected_stocks[region, Commodity.SOYBEAN_MEAL] += (
            result.executed_tonnes * config.meal_yield_fraction
        )
        expected_stocks[region, Commodity.SOYBEAN_OIL] += (
            result.executed_tonnes * config.oil_yield_fraction
        )
    actual_stocks = {
        (item.region, item.commodity): item.quantity_tonnes
        for item in monthly.ending_state.snapshot.inventories
    }
    assert actual_stocks.keys() == expected_stocks.keys(), "inventory coverage changed"
    for key, expected in expected_stocks.items():
        _assert_close(actual_stocks[key], expected, f"{monthly.month}/{key}")


def _validate_history(
    result: SimulationResult, configs: dict[str, CrusherConfig]
) -> None:
    """Check all monthly stocks, unchanged price values, dates, and physical flows."""
    initial_prices = {
        price.commodity: (price.price_usd_per_tonne, price.data_source)
        for price in result.initial_state.snapshot.prices
    }
    for monthly in result.monthly_results:
        for state in (monthly.starting_state, monthly.ending_state):
            assert state.snapshot.month == monthly.month, "state month mismatch"
            assert all(
                math.isfinite(item.quantity_tonnes) and item.quantity_tonnes >= 0
                for item in state.snapshot.inventories
            ), f"{monthly.month}: invalid inventory quantity"
            assert all(
                price.month == monthly.month for price in state.snapshot.prices
            ), f"{monthly.month}: price observation month mismatch"
            assert {
                price.commodity: (price.price_usd_per_tonne, price.data_source)
                for price in state.snapshot.prices
            } == initial_prices, f"{monthly.month}: reference prices changed"
        _validate_settlement(monthly, configs)
    for previous, current in pairwise(result.monthly_results):
        assert (
            previous.ending_state.snapshot.inventories
            == current.starting_state.snapshot.inventories
        ), f"{current.month}: inventories did not carry forward"


def _run_checks(
    state: MarketState, agents: tuple[Agent, ...], configs: dict[str, CrusherConfig]
) -> SimulationResult:
    """Compare independent runs and observe every agent's monthly opportunities."""
    original = state.model_dump_json()
    one_month = run_simulation(state, agents, crusher_configs=configs, months=1)
    annual = run_simulation(state, agents, crusher_configs=configs, months=12)
    repeated = run_simulation(state, agents, crusher_configs=configs, months=12)
    reordered = run_simulation(
        state, reversed(agents), crusher_configs=configs, months=12
    )
    observed = tuple(_ObservedAgent(agent) for agent in agents)
    recorded = run_simulation(state, observed, crusher_configs=configs, months=12)
    assert len(one_month.monthly_results) == 1, "expected one monthly result"
    assert len(annual.monthly_results) == 12, "expected twelve monthly results"
    assert tuple(item.month for item in annual.monthly_results) == _MONTHS
    assert one_month.monthly_results[0] == annual.monthly_results[0]
    print("PASS: one-month and January-December 2025 histories")
    assert all(tuple(agent.months) == _MONTHS for agent in observed)
    assert recorded == annual, "observation must not change agent behavior"
    print("PASS: all 12 agents called once every month, including abstentions")
    for result in (one_month, annual):
        _validate_history(result, configs)
    print("PASS: every decision settled; purchases and sales wholly unexecuted")
    print("PASS: crushing bounded by requests and stock; configured meal/oil yields")
    print("PASS: finite nonnegative inventories, carried into the next month")
    print("PASS: constant global price values with advancing observation months")
    assert state.model_dump_json() == original, "initial state was mutated"
    assert annual == repeated, "identical runs must have identical results"
    assert annual == reordered, "agent ordering must not change results"
    print("PASS: initial state unchanged; repeat and agent-reordering equality")
    return annual


def _print_input(initial: SimulationInput, agents: tuple[Agent, ...]) -> None:
    """Summarize selected source records, baselines, and regional registrations."""
    print(f"USDA: {_USDA_PATH}")
    print(f"World Bank: {_WORLD_BANK_PATH}")
    print(
        f"Marketing year {_MARKETING_YEAR}: {len(initial.balances)} balances; "
        f"{len(initial.inventories)} beginning-stock inventories"
    )
    print(f"Global reference prices ({_PRICE_MONTH}, USD/tonne):")
    for price in initial.prices:
        print(f"  {price.commodity.value:<18} {price.price_usd_per_tonne:>12,.2f}")
    print("Agents by region:")
    for region in sorted(Region):
        names = [
            agent.identity.agent_id
            for agent in agents
            if agent.identity.region == region
        ]
        print(f"  {region.value:<16} {', '.join(names)}")
    print("Crusher: 10,000 t/month, 35 USD/t cost, 80% meal / 20% oil yields")
    print("Trader: 5,000 t/month purchase limit; default decision bands/fractions")
    print("Beginning stocks are not adjusted to January; annual flows are not used.")


def _print_results(result: SimulationResult) -> None:
    """Display monthly regional crushing and stocks plus intention-only totals."""
    print("\nMonthly ending inventories and executed crushing (metric tonnes):")
    print(
        f"{'Month':<8} {'Region':<16} {'Crushed':>12} "
        f"{'Soybeans':>14} {'Meal':>14} {'Oil':>14}"
    )
    for monthly in result.monthly_results:
        stocks = {
            (item.region, item.commodity): item.quantity_tonnes
            for item in monthly.ending_state.snapshot.inventories
        }
        for region in sorted(Region):
            crushed = sum(
                item.executed_tonnes
                for item in monthly.settlement_results
                if item.decision.agent.region == region
                and item.decision.action == DecisionAction.CRUSH
            )
            print(
                f"{monthly.month:%Y-%m}  {region.value:<16} {crushed:>12,.2f} "
                f"{stocks[region, Commodity.SOYBEANS]:>14,.2f} "
                f"{stocks[region, Commodity.SOYBEAN_MEAL]:>14,.2f} "
                f"{stocks[region, Commodity.SOYBEAN_OIL]:>14,.2f}"
            )
    settlements = [
        item for month in result.monthly_results for item in month.settlement_results
    ]
    print("\nTotals across 12 months (tonnes of intentions may recur across months):")
    for action in DecisionAction:
        items = [item for item in settlements if item.decision.action == action]
        requested = sum(item.requested_tonnes for item in items)
        executed = sum(item.executed_tonnes for item in items)
        unexecuted = sum(item.unfulfilled_tonnes for item in items)
        print(
            f"  {action.value:<15} requested={requested:,.2f} "
            f" executed={executed:,.2f}  unexecuted={unexecuted:,.2f}"
        )
    print("\nFinal inventories (December 2025, metric tonnes):")
    for item in result.final_state.snapshot.inventories:
        print(
            f"  {item.region.value:<16} {item.commodity.value:<18} "
            f"{item.quantity_tonnes:>14,.2f}"
        )


def main() -> int:
    """Run the manual acceptance check, reporting missing inputs or failed checks."""
    missing = [path for path in (_USDA_PATH, _WORLD_BANK_PATH) if not path.is_file()]
    if missing:
        for path in missing:
            print(f"FAIL: required local dataset is missing: {path}", file=sys.stderr)
        print(
            "Place the real datasets in data/raw/; see data/README.md. "
            "No download or fixture fallback is performed.",
            file=sys.stderr,
        )
        return 1
    try:
        initial = _load_input()
        state = MarketState.from_snapshot(
            MarketSnapshot(
                month=_PRICE_MONTH,
                prices=initial.prices,
                inventories=initial.inventories,
            )
        )
        agents, configs = _create_agents(initial)
        _print_input(initial, agents)
        result = _run_checks(state, agents, configs)
        _print_results(result)
    except (AssertionError, ValueError, OSError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("\nPASS: all Milestone 2 smoke-test invariants")
    return 0


if __name__ == "__main__":
    sys.exit(main())
