from dataclasses import replace
from datetime import date
from inspect import signature
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from streamlit.testing.v1 import AppTest

from dashboard import data
from dashboard.components import scenario_comparison as view
from scripts.compare_scenarios import create_demo, run_demo
from soybean_sim.models import Commodity, Region
from soybean_sim.scenarios import (
    ArgentinaDroughtConfig,
    MetricComparison,
    ScenarioComparison,
)


@pytest.fixture(scope="module")
def comparison() -> ScenarioComparison:
    """Use the deterministic, in-memory synthetic comparison."""
    return data.load_demo_comparison()


def test_empty_state_and_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserve the component API and prompt for results before drawing charts."""
    assert tuple(signature(view.render).parameters) == ("comparison",)
    ui = MagicMock()
    monkeypatch.setattr(view, "st", ui)
    view.render(None)
    assert "sidebar" in ui.info.call_args.args[0]
    ui.columns.assert_not_called()


def test_actual_production_and_partial_drought(comparison: ScenarioComparison) -> None:
    """Calculate total production loss from records, including partial intervals."""
    whole = view._production_comparison(comparison)
    assert whole is not None
    assert whole.baseline > whole.drought
    assert whole.percentage_difference == pytest.approx(-30)
    case = create_demo()
    partial = run_demo(
        replace(
            case,
            drought_config=ArgentinaDroughtConfig(
                production_reduction_fraction=0.30,
                start_month=date(2025, 1, 1),
                end_month=date(2025, 6, 1),
            ),
        )
    )
    actual = view._production_comparison(partial)
    assert actual is not None
    assert actual.percentage_difference == pytest.approx(-15)
    assert actual.difference == pytest.approx(actual.drought - actual.baseline)


def test_monthly_metrics_and_total_executions(comparison: ScenarioComparison) -> None:
    """Use backend executions and same-month market observations without duplication."""
    months = comparison.monthly_metrics()
    markets = view._markets(months, Region.ARGENTINA, Commodity.SOYBEANS)
    assert [month for month, _ in markets] == sorted(m.month for m in months)
    assert markets[-1][0] == comparison.baseline.monthly_results[-1].month
    final = markets[-1][1]
    expected_price = next(
        p.price_usd_per_tonne
        for p in comparison.baseline.monthly_results[-1].regional_prices
        if p.region == Region.ARGENTINA and p.commodity == Commodity.SOYBEANS
    )
    expected_stock = next(
        i.quantity_tonnes
        for i in comparison.baseline.final_state.snapshot.inventories
        if i.region == Region.ARGENTINA and i.commodity == Commodity.SOYBEANS
    )
    assert final.price_usd_per_tonne.baseline == expected_price
    assert final.inventory_tonnes.baseline == expected_stock
    assert final.price_usd_per_tonne.difference == pytest.approx(
        final.price_usd_per_tonne.drought - expected_price
    )
    total = view._sum_pairs(
        value for _, value in view._crushing(months, Region.ARGENTINA)
    )
    assert total.baseline == sum(
        s.executed_tonnes
        for month in comparison.baseline.monthly_results
        for s in month.settlement_results
    )
    traded = view._sum_pairs(month.trade_volume_tonnes for month in months)
    assert traded.baseline == sum(
        t.quantity_tonnes
        for month in comparison.baseline.monthly_results
        for t in month.executed_trades
    )
    single = view._markets(months[:1], Region.ARGENTINA, Commodity.SOYBEANS)
    assert len(single) == 1


def test_percentage_zero_and_identical_values() -> None:
    """Distinguish a zero difference from an undefined percentage."""
    assert view._percent(MetricComparison(baseline=0, drought=0)) == "Unavailable"
    assert view._percent(MetricComparison(baseline=10, drought=10)) == "0.00%"
    assert view._percent(MetricComparison(baseline=10, drought=12)) == "+20.00%"
    assert view._pair_caption(MetricComparison(baseline=100, drought=90), "t") == (
        "Baseline 100.00 t | Drought 90.00 t | Difference \N{MINUS SIGN}10.00 t"
    )


def test_kpi_card_shows_paired_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Display unchanged baseline and drought totals alongside the impact."""
    ui = MagicMock()
    monkeypatch.setattr(view, "st", ui)
    view._card(
        "Crushing Impact",
        "\N{MINUS SIGN}10.00 t",
        MetricComparison(baseline=100, drought=90),
        "t",
    )
    ui.metric.assert_called_once_with("Crushing Impact", "\N{MINUS SIGN}10.00 t")
    assert [call.args[0] for call in ui.caption.call_args_list] == [
        "Baseline: 100.00 t",
        "Drought: 90.00 t",
    ]


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (67.79, "USD/t", "+67.79 USD/t"),
        (-110, "t", "\N{MINUS SIGN}110.00 t"),
        (0, "t", "0.00 t"),
        (-0.0, "t", "0.00 t"),
        (-0.001, "%", "0.00%"),
        (0.001, "%", "0.00%"),
    ],
)
def test_signed_change_format(value: float, unit: str, expected: str) -> None:
    """Format signs consistently, including values rounding to zero."""
    assert view._change(value, unit) == expected


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (None, None, "Entire simulation"),
        (date(2025, 3, 1), date(2025, 8, 1), "2025-03 to 2025-08"),
        (date(2024, 3, 1), None, "From 2024-03"),
        (None, date(2026, 8, 1), "Through 2026-08"),
    ],
)
def test_drought_period(start: date | None, end: date | None, expected: str) -> None:
    """Describe the configured bounds even when outside the simulation period."""
    assert view._drought_period(start, end) == expected


def test_month_labels_and_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use chronological dates and include years only across calendar years."""
    ui = MagicMock()
    monkeypatch.setattr(view, "st", ui)
    metric = MetricComparison(baseline=1, drought=2)
    months = [(date(2025, 12, 1), metric), (date(2026, 1, 1), metric)]
    assert view._month_format([months[0][0]]) == "%b"
    view._chart(months, "t", bars=False)
    chart = ui.altair_chart.call_args.args[0]
    assert chart.to_dict()["encoding"]["x"]["axis"]["format"] == "%b %Y"
    assert chart.data["Month"].tolist()[:2] == [month for month, _ in months]
    view._chart(months, "t", bars=True)
    chart = ui.altair_chart.call_args.args[0]
    assert chart.to_dict()["encoding"]["x"]["axis"]["format"] == "%b %Y"


def test_zero_drought_and_missing_production(comparison: ScenarioComparison) -> None:
    """Show zero change for an unchanged scenario and retain missing supply."""
    case = create_demo()
    unchanged = run_demo(
        replace(
            case, drought_config=ArgentinaDroughtConfig(production_reduction_fraction=0)
        )
    )
    production = view._production_comparison(unchanged)
    assert production is not None and view._percent(production) == "0.00%"
    month = comparison.baseline.monthly_results[0]
    missing = month.model_copy(update={"market_flows": ()})
    incomplete = comparison.baseline.model_copy(update={"monthly_results": (missing,)})
    assert view._production(incomplete) is None


def test_empty_activity_and_zero_inventory(
    monkeypatch: pytest.MonkeyPatch, comparison: ScenarioComparison
) -> None:
    """Show empty activity states while retaining zero inventory."""
    ui = MagicMock()
    monkeypatch.setattr(view, "st", ui)
    months = comparison.monthly_metrics()
    crushing = view._crushing(months, Region.BRAZIL)
    view._crush_chart(crushing)
    assert any("No soybean crushing" in str(call) for call in ui.info.call_args_list)
    assert not ui.altair_chart.called
    markets = view._markets(months, Region.BRAZIL, Commodity.SOYBEAN_MEAL)
    view._trade(markets)
    assert any("No trades" in str(call) for call in ui.info.call_args_list)
    ui.expander.assert_not_called()
    market = markets[0][1].model_copy(
        update={"inventory_tonnes": MetricComparison(baseline=0, drought=0)}
    )
    view._inventories([(months[0].month, market)])
    assert ui.altair_chart.call_args.args[0].data["Value"].tolist() == [0.0, 0.0]


def test_trade_directions(comparison: ScenarioComparison) -> None:
    """Regional comparisons distinguish the seller's export and buyer's import."""
    months = comparison.monthly_metrics()
    argentina = view._markets(months, Region.ARGENTINA, Commodity.SOYBEANS)[0][1]
    europe = view._markets(months, Region.EUROPEAN_UNION, Commodity.SOYBEANS)[0][1]
    assert argentina.exports_tonnes.baseline == 25
    assert argentina.imports_tonnes.baseline == 0
    assert europe.imports_tonnes.baseline == 25
    assert europe.exports_tonnes.baseline == 0


def test_unchanged_trade_keeps_details_collapsed(
    monkeypatch: pytest.MonkeyPatch, comparison: ScenarioComparison
) -> None:
    """Indicate identical monthly trades and keep their real records available."""
    ui = MagicMock()
    monkeypatch.setattr(view, "st", ui)
    months = comparison.monthly_metrics()
    markets = view._markets(months, Region.EUROPEAN_UNION, Commodity.SOYBEANS)
    identical = [
        (
            month,
            item.model_copy(
                update={
                    "imports_tonnes": MetricComparison(
                        baseline=item.imports_tonnes.baseline,
                        drought=item.imports_tonnes.baseline,
                    ),
                    "exports_tonnes": MetricComparison(
                        baseline=item.exports_tonnes.baseline,
                        drought=item.exports_tonnes.baseline,
                    ),
                }
            ),
        )
        for month, item in markets
    ]
    view._trade(identical)
    assert any(
        "no difference in executed trade" in str(c) for c in ui.info.call_args_list
    )
    ui.expander.assert_called_once_with("Monthly trade details")
    assert len(ui.dataframe.call_args.args[0]) == len(months)


def test_invalid_comparison_is_visible(
    monkeypatch: pytest.MonkeyPatch, comparison: ScenarioComparison
) -> None:
    """An invalid copied backend result produces an error instead of fake metrics."""
    ui = MagicMock()
    monkeypatch.setattr(view, "st", ui)
    malformed = comparison.model_copy(
        update={
            "baseline": comparison.baseline.model_copy(update={"monthly_results": ()})
        }
    )
    view.render(malformed)
    assert "invalid comparison" in ui.error.call_args.args[0]
    ui.metric.assert_not_called()


def test_app_filters_reuse_results(
    monkeypatch: pytest.MonkeyPatch, comparison: ScenarioComparison
) -> None:
    """Filtering and navigation retain demo results without another simulation."""
    loader = Mock(wraps=data.load_demo_comparison)
    monkeypatch.setattr(data, "load_demo_comparison", loader)
    before = comparison.model_dump_json()
    app = AppTest.from_file(
        Path(__file__).resolve().parents[3] / "dashboard/app.py"
    ).run()
    app.sidebar.radio[0].set_value("Scenario Comparison").run()
    assert any("sidebar" in item.value for item in app.info)
    app.button[0].click().run(timeout=30)
    assert not app.exception and len(app.selectbox) == 2
    assert app.selectbox[0].value == Region.ARGENTINA
    assert app.selectbox[1].value == Commodity.SOYBEANS
    assert all(
        "\N{EM DASH}" not in item.value
        for items in (app.caption, app.info, app.success, app.header, app.subheader)
        for item in items
    )
    assert len(app.metric) == 4
    assert [item.label for item in app.metric] == [
        "Production Shock",
        "Crushing Impact",
        "Trade Impact",
        "Price Impact",
    ]
    app.selectbox[1].select(Commodity.SOYBEAN_OIL).run()
    app.selectbox[0].select(Region.BRAZIL).run()
    assert not app.exception
    assert any("No soybean crushing" in item.value for item in app.info)
    app.selectbox[0].select(Region.EUROPEAN_UNION).run()
    app.selectbox[1].select(Commodity.SOYBEAN_MEAL).run()
    assert not app.exception
    assert any("No trades" in item.value for item in app.info)
    app.sidebar.radio[0].set_value("Overview").run()
    app.sidebar.radio[0].set_value("Scenario Comparison").run()
    assert not app.exception
    loader.assert_called_once_with()
    assert comparison.model_dump_json() == before
