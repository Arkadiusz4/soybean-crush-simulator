from datetime import date

import pytest
from pydantic import ValidationError

from soybean_sim.agents import Agent, CrusherConfig
from soybean_sim.market import MarketState
from soybean_sim.models import CommodityPrice, MarketSnapshot
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
