import importlib
from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from dashboard import data
from dashboard.components import market_analytics, overview, scenario_comparison
from soybean_sim.scenarios import ScenarioComparison, ScenarioError
from soybean_sim.simulation import SimulationResult

_APP = Path(__file__).resolve().parents[2] / "dashboard/app.py"


def test_adapter_returns_deterministic_backend_results() -> None:
    """Reuse the existing synthetic demo and share its actual baseline result."""
    first = data.load_demo_comparison()
    assert isinstance(first, ScenarioComparison)
    assert isinstance(first.baseline, SimulationResult)
    assert first == data.load_demo_comparison()
    assert first.baseline == data.load_demo_baseline()


def test_modules_import_without_executing_simulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing the application and opening its empty view cannot start the demo."""
    run = Mock(side_effect=AssertionError("unexpected simulation"))
    monkeypatch.setattr(data, "run_demo", run)
    assert callable(importlib.import_module("dashboard.app").main)
    for component in (overview, market_analytics, scenario_comparison):
        assert callable(component.render)
    app = AppTest.from_file(_APP).run()
    assert not app.exception
    assert any("Load demo results" in item.value for item in app.info)
    run.assert_not_called()


@pytest.mark.parametrize("component", [overview, market_analytics, scenario_comparison])
def test_empty_component_displays_message(
    component: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All independent component contracts accept missing results."""
    info = Mock()
    monkeypatch.setattr(component.st, "info", info)
    monkeypatch.setattr(component.st, "header", Mock())
    monkeypatch.setattr(component.st, "write", Mock())
    component.render(None)
    info.assert_called_once()


def test_load_and_navigation_reuse_one_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Load once on request, then retain the paired models across section changes."""
    load = Mock(wraps=data.load_demo_comparison)
    monkeypatch.setattr(data, "load_demo_comparison", load)
    app = AppTest.from_file(_APP).run()
    assert not app.exception
    load.assert_not_called()
    assert app.sidebar.radio[0].value == "Overview"
    assert any("No results loaded" in item.value for item in app.sidebar.caption)
    assert any("not forecasts" in item.value for item in app.sidebar.caption)
    app.button[0].click().run(timeout=30)
    assert not app.exception
    assert any("demo results loaded" in item.value for item in app.sidebar.caption)
    assert any("baseline loaded" in item.value for item in app.success)
    app.radio[0].set_value("Market Analytics").run()
    assert any("baseline loaded" in item.value for item in app.success)
    app.radio[0].set_value("Scenario Comparison").run()
    assert any("drought results loaded" in item.value for item in app.success)
    load.assert_called_once_with()


@pytest.mark.parametrize(
    "failure", [ScenarioError("synthetic failure"), RuntimeError("unexpected failure")]
)
def test_load_failure_can_be_retried(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """Log failures and allow retries without displaying stale successful results."""
    load = Mock(side_effect=[failure, data.load_demo_comparison(), failure])
    logged = Mock()
    monkeypatch.setattr("logging.exception", logged)
    monkeypatch.setattr(data, "load_demo_comparison", load)
    app = AppTest.from_file(_APP).run()
    app.button[0].click().run()
    assert not app.exception
    assert any("Could not load" in item.value for item in app.error)
    assert not app.success
    logged.assert_called_once_with("Synthetic demonstration failed")
    app.radio[0].set_value("Scenario Comparison").run()
    assert any("Load demo results" in item.value for item in app.info)
    app.button[0].click().run(timeout=30)
    assert not app.exception
    assert any("drought results loaded" in item.value for item in app.success)
    assert load.call_count == 2
    app.button[0].click().run()
    assert not app.success
    assert any("Could not load" in item.value for item in app.error)
    assert logged.call_count == 2
