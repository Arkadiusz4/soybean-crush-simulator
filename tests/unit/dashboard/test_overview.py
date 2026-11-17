from datetime import date
from inspect import signature
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from streamlit.testing.v1 import AppTest

from dashboard.components import overview
from dashboard.data import load_demo_baseline
from soybean_sim.agents import AgentDecision, AgentIdentity, AgentType, DecisionAction
from soybean_sim.market import (
    MarketState,
    SettlementOutcome,
    SettlementResult,
    TradeExecution,
)
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketSnapshot,
    Region,
)
from soybean_sim.simulation import MonthlyResult, SimulationResult


def sample_result(*, missing_soybeans: bool = False) -> SimulationResult:
    """Build a small synthetic partial crush and one physical transfer."""
    month = date(2025, 1, 1)
    price = CommodityPrice(
        commodity=Commodity.SOYBEANS,
        month=month,
        price_usd_per_tonne=100,
        data_source="Fixture source",
    )
    initial = {
        (Region.ARGENTINA, Commodity.SOYBEANS): 25,
        (Region.ARGENTINA, Commodity.SOYBEAN_MEAL): 0,
        (Region.ARGENTINA, Commodity.SOYBEAN_OIL): 0,
        (Region.BRAZIL, Commodity.SOYBEANS): 0,
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL): 0,
        (Region.BRAZIL, Commodity.SOYBEAN_OIL): 0,
        (Region.EUROPEAN_UNION, Commodity.SOYBEANS): 10,
        (Region.EUROPEAN_UNION, Commodity.SOYBEAN_MEAL): 0,
        (Region.EUROPEAN_UNION, Commodity.SOYBEAN_OIL): 0,
    }
    if missing_soybeans:
        del initial[Region.EUROPEAN_UNION, Commodity.SOYBEANS]
    final = {
        **initial,
        (Region.ARGENTINA, Commodity.SOYBEANS): 0,
        (Region.ARGENTINA, Commodity.SOYBEAN_MEAL): 13,
        (Region.ARGENTINA, Commodity.SOYBEAN_OIL): 5,
        (Region.BRAZIL, Commodity.SOYBEAN_MEAL): 7,
    }

    def state(values: dict) -> MarketState:
        """Wrap explicitly supplied fixture stocks in a market snapshot."""
        return MarketState.from_snapshot(
            MarketSnapshot(
                month=month,
                prices=(price,),
                inventories=tuple(
                    Inventory(region=region, commodity=commodity, quantity_tonnes=value)
                    for (region, commodity), value in values.items()
                ),
            )
        )

    initial_state, ending_state = state(initial), state(final)
    decision = AgentDecision(
        agent=AgentIdentity(
            agent_id="crusher-ar", agent_type=AgentType.CRUSHER, region=Region.ARGENTINA
        ),
        month=month,
        action=DecisionAction.CRUSH,
        commodity=Commodity.SOYBEANS,
        quantity_tonnes=100,
    )
    settlement = SettlementResult(
        decision=decision,
        requested_tonnes=100,
        executed_tonnes=25,
        unfulfilled_tonnes=75,
        outcome=SettlementOutcome.CRUSH_SETTLED,
    )
    trade = TradeExecution(
        month=month,
        seller_agent_id="seller",
        buyer_agent_id="buyer",
        source_region=Region.ARGENTINA,
        destination_region=Region.BRAZIL,
        commodity=Commodity.SOYBEAN_MEAL,
        quantity_tonnes=7,
    )
    monthly = MonthlyResult(
        month=month,
        starting_state=initial_state,
        ending_state=ending_state,
        decisions=(decision,),
        settlement_results=(settlement,),
        executed_trades=(trade,),
        physical_cycle=True,
    )
    return SimulationResult(
        initial_state=initial_state,
        monthly_results=(monthly,),
        final_state=ending_state,
    )


def test_empty_state_and_public_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the shared render contract and avoid displaying invented metrics."""
    assert tuple(signature(overview.render).parameters) == ("result",)
    ui = MagicMock()
    monkeypatch.setattr(overview, "st", ui)
    overview.render(None)
    ui.header.assert_called_once_with("Market Overview")
    assert "Load demo results" in ui.info.call_args.args[0]
    ui.columns.assert_not_called()
    ui.dataframe.assert_not_called()


def test_executed_metrics_and_regional_stocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Count settled crush and transfers, retaining zero-activity regions."""
    result = sample_result()
    before = result.model_dump_json()
    ui = MagicMock()
    cards = [MagicMock() for _ in range(4)]
    ui.columns.side_effect = [cards, [MagicMock() for _ in range(3)]]
    monkeypatch.setattr(overview, "st", ui)
    overview.render(result)
    assert [call.args for call in ui.metric.call_args_list] == [
        ("Executed crushing (t)", "25.00"),
        ("Interregional trade (t)", "7.00"),
        ("Executed trades", "1"),
        ("Simulated months", "1"),
        ("Soybeans (t)", "10.00"),
        ("Soybean Meal (t)", "20.00"),
        ("Soybean Oil (t)", "5.00"),
    ]
    assert "2025-01 to 2025-01" in ui.write.call_args.args[0]
    assert "3 regions" in ui.write.call_args.args[0]
    assert "Fixture source" in ui.caption.call_args_list[0].args[0]
    rows = ui.dataframe.call_args_list[0].args[0]
    assert [row["Region"] for row in rows] == ["Argentina", "Brazil", "European Union"]
    assert rows[0]["Executed crushing (t)"] == "25.00"
    assert rows[0]["Exports (t)"] == rows[1]["Imports (t)"] == "7.00"
    assert rows[2]["Imports (t)"] == rows[2]["Executed crushing (t)"] == "0.00"
    assert rows[2]["Final soybean inventory (t)"] == "10.00"
    assert result.model_dump_json() == before
    ui.columns.side_effect = [cards, [MagicMock() for _ in range(3)]]
    overview.render(result)
    assert (
        ui.dataframe.call_args_list[0].args[0] == ui.dataframe.call_args_list[1].args[0]
    )


def test_missing_inventory_is_not_invented(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mark missing regional and incomplete aggregate inventories as unavailable."""
    ui = MagicMock()
    ui.columns.side_effect = [
        [MagicMock() for _ in range(4)],
        [MagicMock() for _ in range(3)],
    ]
    monkeypatch.setattr(overview, "st", ui)
    overview.render(sample_result(missing_soybeans=True))
    assert (
        ui.dataframe.call_args_list[0].args[0][2]["Final soybean inventory (t)"]
        == "Unavailable"
    )
    assert ui.metric.call_args_list[4].args == ("Soybeans (t)", "Unavailable")


def test_shared_demo_in_app_and_navigation() -> None:
    """Render the real demo and retain its metrics when navigating between sections."""
    original = load_demo_baseline()
    app = AppTest.from_file(
        Path(__file__).resolve().parents[3] / "dashboard/app.py"
    ).run()
    assert not app.exception and len(app.metric) == 0
    app.button[0].click().run(timeout=30)
    assert not app.exception
    assert [item.value for item in app.metric] == [
        "960.00",
        "575.00",
        "12",
        "12",
        "7,840.00",
        "4,048.00",
        "3,952.00",
    ]
    assert app.dataframe[0].value.shape[0] == 4
    app.radio[0].set_value("Market Analytics").run()
    assert not app.exception
    app.radio[0].set_value("Scenario Comparison").run()
    assert not app.exception
    app.radio[0].set_value("Overview").run()
    assert not app.exception and app.metric[0].value == "960.00"
    assert original == load_demo_baseline()
