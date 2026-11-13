from datetime import date

import pytest
from pydantic import ValidationError

from soybean_sim.agents import Agent, CrusherConfig, DecisionAction
from soybean_sim.market import (
    MarketFlow,
    MarketFlowType,
    MarketPressure,
    MarketState,
    RegionalPrice,
    TradeExecution,
)
from soybean_sim.models import Commodity, CommodityPrice, MarketSnapshot, Region
from soybean_sim.simulation import MonthlyResult, SimulationResult, run_simulation


@pytest.fixture
def result(
    state: MarketState, agents: tuple[Agent, ...], configs: dict[str, CrusherConfig]
) -> SimulationResult:
    """Provide two completed synthetic months for history consistency validation."""
    return run_simulation(state, agents, crusher_configs=configs, months=2)


def test_frozen_results_round_trip(result: SimulationResult) -> None:
    """Round-trip histories while preserving immutable result models."""
    assert SimulationResult.model_validate_json(result.model_dump_json()) == result
    monthly = result.monthly_results[0]
    assert MonthlyResult.model_validate_json(monthly.model_dump_json()) == monthly
    assert isinstance(result.monthly_results, tuple)
    with pytest.raises(ValidationError, match="frozen_instance"):
        result.final_state = result.initial_state
    with pytest.raises(ValidationError, match="frozen_instance"):
        monthly.month = date(2024, 1, 1)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SimulationResult.model_validate({**result.model_dump(), "random_seed": 1})


@pytest.mark.parametrize("field", ["month", "starting_state", "ending_state"])
def test_monthly_states_share_the_recorded_month(
    result: SimulationResult, field: str
) -> None:
    """Reject inconsistent monthly labels and boundary state dates."""
    first, second = result.monthly_results
    changes = {
        "month": {"month": date(2023, 10, 1)},
        "starting_state": {"starting_state": second.starting_state},
        "ending_state": {"ending_state": second.ending_state},
    }
    with pytest.raises(ValidationError, match="states must match the result month"):
        MonthlyResult.model_validate({**first.model_dump(), **changes[field]})


def test_decision_month_matches_record(result: SimulationResult) -> None:
    """Keep all recorded intentions in the month of their observed starting state."""
    monthly = result.monthly_results[0]
    decision = monthly.decisions[0].model_copy(update={"month": date(2023, 12, 1)})
    with pytest.raises(ValidationError, match="decisions must match the result month"):
        MonthlyResult.model_validate(
            {**monthly.model_dump(), "decisions": (decision, *monthly.decisions[1:])}
        )


@pytest.mark.parametrize("defect", ["missing", "duplicate"])
def test_settlements_cover_decisions(result: SimulationResult, defect: str) -> None:
    """Require one settlement per decision, preserving duplicate counts."""
    monthly = result.monthly_results[0]
    settlements = (
        monthly.settlement_results[1:]
        if defect == "missing"
        else (*monthly.settlement_results, monthly.settlement_results[0])
    )
    with pytest.raises(ValidationError, match="cover every decision exactly once"):
        MonthlyResult.model_validate(
            {**monthly.model_dump(), "settlement_results": settlements}
        )


def test_reference_prices_cannot_change_within_month(result: SimulationResult) -> None:
    """Enforce the current engine's constant-price policy in monthly records."""
    monthly = result.monthly_results[0]
    snapshot = monthly.ending_state.snapshot
    ending = MarketState.from_snapshot(
        MarketSnapshot(
            month=snapshot.month,
            prices=(
                snapshot.prices[0].model_copy(update={"price_usd_per_tonne": 500}),
                *snapshot.prices[1:],
            ),
            inventories=snapshot.inventories,
        )
    )
    with pytest.raises(ValidationError, match="reference prices must remain unchanged"):
        MonthlyResult.model_validate({**monthly.model_dump(), "ending_state": ending})


def test_history_cannot_be_empty(result: SimulationResult) -> None:
    """Require at least one completed month in an overall simulation result."""
    with pytest.raises(ValidationError, match="monthly_results"):
        SimulationResult.model_validate({**result.model_dump(), "monthly_results": ()})


@pytest.mark.parametrize("field", ["initial_state", "final_state"])
def test_overall_boundaries_match_history(result: SimulationResult, field: str) -> None:
    """Keep initial and final state fields consistent with monthly history."""
    changed = result.final_state if field == "initial_state" else result.initial_state
    with pytest.raises(ValidationError, match=field):
        SimulationResult.model_validate({**result.model_dump(), field: changed})


@pytest.mark.parametrize("defect", ["month", "inventory", "price", "source"])
def test_monthly_history_continuity(result: SimulationResult, defect: str) -> None:
    """Reject gaps, reset stocks, changed price values, and changed source labels."""
    first, second = result.monthly_results
    source = second.starting_state.snapshot
    month = date(2024, 1, 1) if defect == "month" else source.month
    prices = tuple(
        CommodityPrice(
            commodity=price.commodity,
            month=month,
            price_usd_per_tonne=500 if defect == "price" else price.price_usd_per_tonne,
            data_source="changed source" if defect == "source" else price.data_source,
        )
        for price in source.prices
    )
    starting = MarketState.from_snapshot(
        MarketSnapshot(
            month=month,
            prices=prices,
            inventories=first.starting_state.snapshot.inventories
            if defect == "inventory"
            else source.inventories,
        )
    )
    # Empty intentions isolate boundary continuity from per-month decision matching.
    changed = MonthlyResult(
        month=month,
        starting_state=starting,
        decisions=(),
        settlement_results=(),
        ending_state=starting,
    )
    with pytest.raises(
        ValidationError, match="history must carry stocks and constant prices"
    ):
        SimulationResult(
            initial_state=result.initial_state,
            monthly_results=(first, changed),
            final_state=changed.ending_state,
        )


def test_physical_mode_retains_only_crushing_settlement_results(
    result: SimulationResult,
) -> None:
    """Distinguish physical trade intentions from legacy deferred settlements.

    Args:
        result: Legacy month containing crush and trade decisions.

    Returns:
        None: Confirms mode-specific coverage without weakening legacy validation.
    """
    monthly = result.monthly_results[0]
    crushing = tuple(
        item
        for item in monthly.settlement_results
        if item.decision.action == DecisionAction.CRUSH
    )
    physical = MonthlyResult.model_validate(
        {**monthly.model_dump(), "physical_cycle": True, "settlement_results": crushing}
    )
    assert physical.decisions == monthly.decisions
    assert physical.market_flows == physical.executed_trades == ()
    assert MonthlyResult.model_validate_json(physical.model_dump_json()) == physical
    with pytest.raises(ValidationError, match="cover every decision exactly once"):
        MonthlyResult.model_validate(
            {**monthly.model_dump(), "settlement_results": crushing}
        )
    with pytest.raises(
        ValidationError, match="cover every CRUSH decision exactly once"
    ):
        MonthlyResult.model_validate({**monthly.model_dump(), "physical_cycle": True})
    assert not monthly.physical_cycle
    assert monthly.market_flows == monthly.executed_trades == ()


@pytest.mark.parametrize("field", ["market_flows", "executed_trades"])
def test_physical_activity_requires_mode_and_matching_month(
    result: SimulationResult, field: str
) -> None:
    """Validate the temporal and mode semantics of new history fields.

    Args:
        result: Existing legacy history fixture.
        field: Physical activity field to populate.

    Returns:
        None: Confirms legacy records cannot claim physical executions or flows.
    """
    monthly = result.monthly_results[0]
    flow = MarketFlow(
        month=monthly.month,
        region=Region.BRAZIL,
        commodity=Commodity.SOYBEANS,
        flow_type=MarketFlowType.SUPPLY,
        quantity_tonnes=10,
    )
    trade = TradeExecution(
        month=monthly.month,
        source_region=Region.BRAZIL,
        destination_region=Region.UNITED_STATES,
        seller_agent_id="seller",
        buyer_agent_id="buyer",
        commodity=Commodity.SOYBEANS,
        quantity_tonnes=10,
    )
    record = flow if field == "market_flows" else trade
    with pytest.raises(ValidationError, match="physical_cycle=True"):
        MonthlyResult.model_validate({**monthly.model_dump(), field: (record,)})
    physical = {
        **monthly.model_dump(),
        "physical_cycle": True,
        "settlement_results": tuple(
            item
            for item in monthly.settlement_results
            if item.decision.action == DecisionAction.CRUSH
        ),
    }
    MonthlyResult.model_validate({**physical, field: (record,)})
    with pytest.raises(ValidationError, match="must match the result month"):
        MonthlyResult.model_validate(
            {
                **physical,
                field: (record.model_copy(update={"month": date(2024, 1, 1)}),),
            }
        )


@pytest.mark.parametrize("field", ["regional_prices", "market_pressures"])
def test_regional_history_requires_unique_current_month_records(
    result: SimulationResult, field: str
) -> None:
    """Validate regional history while retaining existing result defaults.

    Args:
        result (SimulationResult): Existing legacy history.
        field (str): Regional observation field being validated.

    Returns:
        None: Confirms timing, uniqueness, mode rules, and JSON serialization.
    """
    monthly = result.monthly_results[0]
    assert monthly.regional_prices == monthly.market_pressures == ()
    record = (
        RegionalPrice(
            month=monthly.month,
            region=Region.BRAZIL,
            commodity=Commodity.SOYBEANS,
            price_usd_per_tonne=250,
        )
        if field == "regional_prices"
        else MarketPressure(
            month=monthly.month,
            region=Region.BRAZIL,
            commodity=Commodity.SOYBEANS,
            supply_tonnes=0,
            demand_tonnes=10,
        )
    )
    if field == "market_pressures":
        with pytest.raises(ValidationError, match="pressures require physical_cycle"):
            MonthlyResult.model_validate({**monthly.model_dump(), field: (record,)})
    else:
        legacy = MonthlyResult.model_validate(
            {**monthly.model_dump(), field: (record,)}
        )
        assert legacy.regional_prices == (record,)
        assert legacy.market_pressures == ()
    physical = {
        **monthly.model_dump(),
        "physical_cycle": True,
        "settlement_results": tuple(
            item
            for item in monthly.settlement_results
            if item.decision.action == DecisionAction.CRUSH
        ),
        field: (record,),
    }
    validated = MonthlyResult.model_validate(physical)
    assert MonthlyResult.model_validate_json(validated.model_dump_json()) == validated
    with pytest.raises(ValidationError, match="must match the result month"):
        MonthlyResult.model_validate(
            {
                **physical,
                field: (record.model_copy(update={"month": date(2024, 1, 1)}),),
            }
        )
    with pytest.raises(ValidationError, match="duplicate"):
        MonthlyResult.model_validate({**physical, field: (record, record)})
    quantity_field = (
        "price_usd_per_tonne" if field == "regional_prices" else "demand_tonnes"
    )
    with pytest.raises(ValidationError, match="duplicate"):
        MonthlyResult.model_validate(
            {
                **physical,
                field: (record, record.model_copy(update={quantity_field: 20})),
            }
        )
