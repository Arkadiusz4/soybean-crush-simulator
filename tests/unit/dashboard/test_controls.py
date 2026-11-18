from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from dashboard import data


def test_outdated_session_result_requests_new_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discard a legacy session result without executing or inventing its settings."""
    execute = Mock(wraps=data.run_dashboard_simulation)
    monkeypatch.setattr(data, "run_dashboard_simulation", execute)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[3] / "dashboard/app.py"
    ).run()
    app.session_state["dashboard_result"] = SimpleNamespace(
        config=SimpleNamespace(
            source="Synthetic", mode="Baseline", reduction_percent=30
        )
    )
    app.run()
    assert not app.exception
    assert app.session_state["dashboard_result"] is None
    assert any("older configuration" in item.value for item in app.warning)
    execute.assert_not_called()


def test_synthetic_modes_and_reduction() -> None:
    """Run the real synthetic backend with baseline and selected drought levels."""
    baseline = data.run_dashboard_simulation(data.RunConfig("Synthetic", "Baseline"))
    zero = data.run_dashboard_simulation(
        data.RunConfig("Synthetic", "Baseline vs Drought", 0)
    )
    severe = data.run_dashboard_simulation(
        data.RunConfig("Synthetic", "Baseline vs Drought", 60)
    )
    assert baseline.comparison is None
    assert zero.comparison is not None and severe.comparison is not None
    assert zero.baseline == zero.comparison.drought == baseline.baseline
    assert severe.comparison.drought_config.percentage_change == -60
    assert severe.baseline is severe.comparison.baseline
    assert severe.comparison.drought != severe.baseline


def test_controls_are_explicit_and_preserve_last_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the run button executes; changes and failed runs keep old results."""
    execute = Mock(wraps=data.run_dashboard_simulation)
    monkeypatch.setattr(data, "run_dashboard_simulation", execute)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[3] / "dashboard/app.py"
    ).run()
    execute.assert_not_called()
    app.button[0].click().run(timeout=30)
    assert not app.exception
    assert execute.call_count == 1
    app.sidebar.selectbox[1].select("Production Shock").run()
    app.sidebar.selectbox[3].select("Compare with Baseline").run()
    app.slider[0].set_value(-60).run()
    assert execute.call_count == 1
    assert any("not been run" in item.value for item in app.caption)
    execute.side_effect = ValueError("failed")
    app.button[0].click().run()
    assert any("failed" in item.value for item in app.error)
    assert any("Last completed run" in item.value for item in app.caption)


def test_advanced_settings_change_simulation() -> None:
    """Pass custom pricing and an inclusive partial drought to real execution."""
    from datetime import date

    standard = data.run_dashboard_simulation(
        data.RunConfig("Synthetic", "Baseline vs Drought")
    )
    custom_config = data.RunConfig(
        "Synthetic",
        "Baseline vs Drought",
        60,
        0.0,
        0.05,
        date(2025, 3, 1),
        date(2025, 3, 1),
    )
    custom = data.run_dashboard_simulation(custom_config)
    assert custom.comparison is not None and standard.comparison is not None
    assert custom.comparison.drought_config.start_month == date(2025, 3, 1)
    assert custom.comparison.drought_config.end_month == date(2025, 3, 1)
    assert custom.baseline != standard.baseline
    assert custom == data.run_dashboard_simulation(custom_config)
    for month in custom.comparison.drought.monthly_results:
        base = next(
            item
            for item in custom.baseline.monthly_results
            if item.month == month.month
        )
        assert (month.market_flows != base.market_flows) == (
            month.month == date(2025, 3, 1)
        )


def test_invalid_drought_and_baseline_normalization() -> None:
    """Reject reversed bounds while ignoring drought controls for a baseline."""
    from datetime import date

    config = data.RunConfig(
        "Synthetic",
        "Baseline vs Drought",
        30,
        0.2,
        0.2,
        date(2025, 5, 1),
        date(2025, 3, 1),
    )
    with pytest.raises(ValueError):
        data.run_dashboard_simulation(config)
    assert data.RunConfig(
        "Synthetic", "Baseline", 99, 0.2, 0.2, date(2025, 3, 1)
    ) == data.RunConfig("Synthetic", "Baseline")


def test_advanced_widgets_do_not_run_and_revert_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mark changes as pending until the selected effective settings match again."""
    execute = Mock(wraps=data.run_dashboard_simulation)
    monkeypatch.setattr(data, "run_dashboard_simulation", execute)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[3] / "dashboard/app.py"
    ).run()
    app.button[0].click().run(timeout=30)
    app.number_input[0].set_value(0.4).run()
    app.number_input[1].set_value(10.0).run()
    assert execute.call_count == 1
    assert any("not been run" in item.value for item in app.caption)
    app.number_input[0].set_value(0.2).run()
    app.number_input[1].set_value(20.0).run()
    assert not any("not been run" in item.value for item in app.caption)
    app.sidebar.radio[0].set_value("Market Analytics").run()
    assert execute.call_count == 1


@pytest.mark.parametrize("year", [2024, 2025])
def test_historical_year_mapping(monkeypatch: pytest.MonkeyPatch, year: int) -> None:
    """Use the preceding USDA marketing year and the selected January price."""
    from datetime import date

    load = Mock(return_value=object())
    monkeypatch.setattr(data, "load_historical_market", load)
    data._load_year(year)
    assert load.call_args.kwargs["marketing_year"] == str(year - 1)
    assert load.call_args.kwargs["price_month"] == date(year, 1, 1)
    assert load.call_args.kwargs["start_month"] == date(year, 1, 1)


def test_year_discovery_excludes_incomplete_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Offer only years whose selected inputs and crusher calibration are complete."""
    usda, bank = tmp_path / "usda.csv", tmp_path / "prices.xlsx"
    usda.touch()
    bank.touch()
    monkeypatch.setattr(data, "_historical_paths", lambda: (usda, bank))
    from datetime import date

    monkeypatch.setattr(
        data,
        "load_usda_psd_csv",
        Mock(
            return_value=[
                SimpleNamespace(marketing_year="2022"),
                SimpleNamespace(marketing_year="2023"),
                SimpleNamespace(marketing_year="2024"),
            ]
        ),
    )
    monkeypatch.setattr(
        data,
        "load_world_bank_prices",
        Mock(
            return_value=[
                SimpleNamespace(month=date(2023, 1, 1)),
                SimpleNamespace(month=date(2024, 1, 1)),
                SimpleNamespace(month=date(2025, 1, 1)),
            ]
        ),
    )

    def load(*args, **kwargs):
        if kwargs["marketing_year"] == "2023":
            raise ValueError("incomplete prices")
        return object()

    monkeypatch.setattr(data, "prepare_historical_market", load)
    monkeypatch.setattr(data, "calibrate_historical_crushers", Mock())
    data._validated_years.cache_clear()
    assert data.supported_historical_years() == (2023, 2025)
    usda.unlink()
    assert data.supported_historical_years() == ()
    data._validated_years.cache_clear()


def test_synthetic_single_drought_and_year_validation() -> None:
    """Execute one shocked result and reject invented years."""
    from datetime import date

    config = data.RunConfig(
        "Synthetic",
        "Argentina Drought",
        60,
        drought_start=date(2025, 3, 1),
        drought_end=date(2025, 3, 1),
    )
    single = data.run_dashboard_simulation(config)
    paired = data.run_dashboard_simulation(
        data.RunConfig(
            "Synthetic",
            "Baseline vs Drought",
            60,
            drought_start=date(2025, 3, 1),
            drought_end=date(2025, 3, 1),
        )
    )
    assert paired.comparison is not None
    assert single.comparison is None and single.baseline == paired.comparison.drought
    with pytest.raises(ValueError, match="only for 2025"):
        data.run_dashboard_simulation(
            data.RunConfig("Synthetic", "Baseline", year=2024)
        )
    with pytest.raises(ValueError, match="unsupported"):
        data.run_dashboard_simulation(
            data.RunConfig("Historical USDA/World Bank", "Baseline", year=1900)
        )


def test_stale_historical_runner_reports_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explain stale Streamlit imports before preparing a drought run."""

    def old_runner(prepared, *, crusher_configs, trader_configs, price_dynamics_config):
        return None

    monkeypatch.setattr(data, "run_historical_market", old_runner)
    with pytest.raises(RuntimeError, match="restart the Streamlit server"):
        data.run_dashboard_simulation(
            data.RunConfig("Historical USDA/World Bank", "Argentina Drought")
        )


def test_historical_year_controls_keep_last_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing years and a failed run retain the prior configuration and result."""
    execute = Mock(wraps=data.run_dashboard_simulation)
    monkeypatch.setattr(data, "run_dashboard_simulation", execute)
    monkeypatch.setattr(data, "supported_historical_years", lambda: (2024, 2025))
    app = AppTest.from_file(
        Path(__file__).resolve().parents[3] / "dashboard/app.py"
    ).run()
    app.button[0].click().run(timeout=30)
    app.sidebar.selectbox[0].select("Historical USDA/World Bank").run()
    assert app.sidebar.selectbox[1].value == 2025
    app.sidebar.selectbox[1].select(2024).run()
    app.sidebar.selectbox[2].select("Production Shock").run()
    assert app.sidebar.selectbox[5].value.year == 2024
    assert app.sidebar.selectbox[6].value.year == 2024
    assert execute.call_count == 1
    assert any("not been run" in item.value for item in app.caption)
    execute.side_effect = ValueError("unavailable year")
    app.button[0].click().run()
    assert any("unavailable year" in item.value for item in app.error)
    assert app.session_state["dashboard_result"].config.year == 2025


def test_regional_shock_controls_run_only_on_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep Brazil shocks single-run and retain the selected change in metadata."""
    from soybean_sim.models import Region

    execute = Mock(wraps=data.run_dashboard_simulation)
    monkeypatch.setattr(data, "run_dashboard_simulation", execute)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[3] / "dashboard/app.py"
    ).run()
    app.sidebar.selectbox[1].select("Production Shock").run()
    app.sidebar.selectbox[2].select(Region.BRAZIL).run()
    app.slider[0].set_value(20).run()
    execute.assert_not_called()
    assert app.sidebar.selectbox[3].options == [
        "Single Simulation",
        "Compare with Baseline",
    ]
    app.button[0].click().run(timeout=30)
    assert not app.exception
    assert app.session_state["dashboard_result"].config.shock_region == Region.BRAZIL
    assert app.session_state["dashboard_result"].config.shock_percent == 20
    execute.assert_called_once()


@pytest.mark.parametrize(
    ("region", "change"),
    [
        (data.Region.ARGENTINA, -30),
        (data.Region.BRAZIL, -20),
        (data.Region.UNITED_STATES, 45),
        (data.Region.EUROPEAN_UNION, 20),
        (data.Region.BRAZIL, 0),
    ],
)
def test_regional_comparison_uses_real_engine(region: data.Region, change: int) -> None:
    """Run both physical scenarios with shared initial state and selected flows."""
    from datetime import date

    config = data.RunConfig(
        "Synthetic",
        "Baseline vs Drought",
        shock_region=region,
        shock_percent=change,
        drought_start=date(2025, 3, 1),
        drought_end=date(2025, 3, 1),
    )
    result = data.run_dashboard_simulation(config)
    assert result.comparison is not None
    assert (
        type(result.comparison).model_validate(result.comparison.model_dump())
        == result.comparison
    )
    assert result.comparison.drought.initial_state == result.baseline.initial_state
    assert result.comparison.drought_config.region == region
    for base, modified in zip(
        result.baseline.monthly_results,
        result.comparison.drought.monthly_results,
        strict=True,
    ):
        for left, right in zip(base.market_flows, modified.market_flows, strict=True):
            targeted = (
                left.region == region
                and left.month == date(2025, 3, 1)
                and left.commodity == data.Commodity.SOYBEANS
                and left.flow_type.value == "supply"
            )
            assert right.quantity_tonnes == pytest.approx(
                left.quantity_tonnes * (1 + change / 100)
                if targeted
                else left.quantity_tonnes
            )
    assert result == data.run_dashboard_simulation(config)


def test_comparison_rejects_unrelated_flow_change() -> None:
    """Reject modified non-target input flows even when the shock itself is valid."""
    from pydantic import ValidationError

    result = data.run_dashboard_simulation(
        data.RunConfig(
            "Synthetic",
            "Baseline vs Drought",
            shock_region=data.Region.BRAZIL,
            shock_percent=20,
        )
    )
    comparison = result.comparison
    assert comparison is not None
    first = comparison.drought.monthly_results[0]
    index = next(
        i
        for i, flow in enumerate(first.market_flows)
        if flow.region != data.Region.BRAZIL
    )
    flows = list(first.market_flows)
    flows[index] = flows[index].model_copy(
        update={"quantity_tonnes": flows[index].quantity_tonnes + 1}
    )
    changed = first.model_copy(update={"market_flows": tuple(flows)})
    history = comparison.drought.model_copy(
        update={"monthly_results": (changed, *comparison.drought.monthly_results[1:])}
    )
    with pytest.raises(ValidationError, match="production shock"):
        type(comparison)(
            baseline=comparison.baseline,
            drought=history,
            drought_config=comparison.drought_config,
        )


def test_regional_comparison_dashboard_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Show the selected regional shock and reuse it across dashboard navigation."""
    execute = Mock(wraps=data.run_dashboard_simulation)
    monkeypatch.setattr(data, "run_dashboard_simulation", execute)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[3] / "dashboard/app.py"
    ).run()
    app.sidebar.selectbox[1].select("Production Shock").run()
    app.sidebar.selectbox[2].select(data.Region.BRAZIL).run()
    app.slider[0].set_value(-20).run()
    app.sidebar.selectbox[3].select("Compare with Baseline").run()
    execute.assert_not_called()
    app.button[0].click().run(timeout=30)
    app.sidebar.radio[0].set_value("Scenario Comparison").run()
    assert not app.exception
    assert any("Brazil soybean production" in item.value for item in app.caption)
    assert any("Modified Scenario" in item.value for item in app.caption)
    assert app.metric[0].value == "\N{MINUS SIGN}20.00%"
    execute.assert_called_once()


def test_shock_results_reach_views_and_slider_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route single and paired results to the appropriate views."""
    from dashboard.components import market_analytics, overview, scenario_comparison
    from soybean_sim.models import Region

    execute = Mock(wraps=data.run_dashboard_simulation)
    show_overview, show_market, show_comparison = Mock(), Mock(), Mock()
    monkeypatch.setattr(data, "run_dashboard_simulation", execute)
    monkeypatch.setattr(overview, "render", show_overview)
    monkeypatch.setattr(market_analytics, "render", show_market)
    monkeypatch.setattr(scenario_comparison, "render", show_comparison)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[3] / "dashboard/app.py"
    ).run()
    app.sidebar.selectbox[1].select("Production Shock").run()
    app.sidebar.selectbox[2].select(Region.BRAZIL).run()
    assert (app.slider[0].min, app.slider[0].max, app.slider[0].step) == (-100, 100, 5)
    app.slider[0].set_value(-50).run()
    execute.assert_not_called()
    app.button[0].click().run(timeout=30)
    single = app.session_state["dashboard_result"]
    assert (
        single.config.shock_percent == -50
        and single.config.shock_region == Region.BRAZIL
    )
    assert show_overview.call_args.args[0] is single.baseline
    app.sidebar.radio[0].set_value("Market Analytics").run()
    assert show_market.call_args.args[0] is single.baseline
    app.sidebar.selectbox[3].select("Compare with Baseline").run()
    assert execute.call_count == 1
    app.button[0].click().run(timeout=30)
    paired = app.session_state["dashboard_result"]
    assert paired is not single and paired.comparison is not None
    assert paired.baseline is paired.comparison.baseline
    assert show_market.call_args.args[0] is paired.baseline
    app.sidebar.radio[0].set_value("Scenario Comparison").run()
    assert show_comparison.call_args.args[0] is paired.comparison
    assert execute.call_count == 2
    execute.side_effect = ValueError("expected failure")
    app.button[0].click().run()
    assert app.session_state["dashboard_result"] is paired
    assert any("expected failure" in error.value for error in app.error)


def test_stress_test_selection_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retain an extreme saved run and its independent control when switching modes."""
    execute = Mock(wraps=data.run_dashboard_simulation)
    monkeypatch.setattr(data, "run_dashboard_simulation", execute)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[3] / "dashboard/app.py"
    ).run()
    app.sidebar.selectbox[1].select("Production Shock").run()
    app.checkbox[0].check().run()
    app.number_input[0].set_value(150.0).run()
    app.button[0].click().run(timeout=30)
    assert app.session_state["dashboard_result"].config.shock_percent == 150
    app.checkbox[0].uncheck().run()
    assert app.session_state["dashboard_result"].config.shock_percent == 150
    assert any("stress-test value" in caption.value for caption in app.caption)
    app.checkbox[0].check().run()
    assert app.number_input[0].value == 150
    execute.assert_called_once()
