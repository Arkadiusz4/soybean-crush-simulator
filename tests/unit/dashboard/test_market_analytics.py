import json
from datetime import date
from inspect import signature
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from dashboard import data
from dashboard.components import market_analytics as analytics
from soybean_sim.models import Commodity, Region
from soybean_sim.simulation import SimulationResult


@pytest.fixture(scope="module")
def demo() -> SimulationResult:
    """Provide the existing deterministic in-memory demonstration."""
    return data.load_demo_baseline()


def test_contract_and_empty_state() -> None:
    """Keep the shared render signature and avoid charts before loading."""
    assert tuple(signature(analytics.render).parameters) == ("result",)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[3] / "dashboard/app.py"
    ).run()
    app.sidebar.radio[0].set_value("Market Analytics").run()
    assert not app.exception
    assert any("sidebar" in item.value for item in app.info)
    assert not app.main.selectbox


def test_prices_and_inventory_use_monthly_observations(demo: SimulationResult) -> None:
    """Keep prices and ending stocks in order without adding a future month."""
    region, commodity = Region.ARGENTINA, Commodity.SOYBEANS
    prices = analytics._price_series(demo, region, commodity)
    stocks = analytics._inventory_series(demo, region, commodity)
    assert [month for month, _ in prices] == [m.month for m in demo.monthly_results]
    assert [month for month, _ in stocks] == [m.month for m in demo.monthly_results]
    for monthly, (_, price), (_, stock) in zip(
        demo.monthly_results, prices, stocks, strict=True
    ):
        assert price == next(
            p.price_usd_per_tonne
            for p in monthly.regional_prices
            if p.region == region and p.commodity == commodity
        )
        assert stock == next(
            i.quantity_tonnes
            for i in monthly.ending_state.snapshot.inventories
            if i.region == region and i.commodity == commodity
        )
    assert prices[-1][0] == demo.monthly_results[-1].month
    assert analytics._price_series(demo, region, Commodity.SOYBEAN_MEAL) != prices


def test_executed_crushing_and_zero_months(demo: SimulationResult) -> None:
    """Use settled quantities rather than requests, and retain inactive months."""
    month = demo.monthly_results[0]
    settlement = month.settlement_results[0].model_copy(update={"executed_tonnes": 5.0})
    changed_month = month.model_copy(update={"settlement_results": (settlement,)})
    changed = demo.model_copy(update={"monthly_results": (changed_month,)})
    assert analytics._crushing_series(changed, Region.ARGENTINA) == [(month.month, 5.0)]
    assert analytics._crushing_series(changed, Region.BRAZIL) == [(month.month, 0.0)]


def test_trade_directions_and_zeroes(demo: SimulationResult) -> None:
    """Count the same physical trade once as each region's import or export."""
    month = demo.monthly_results[0]
    first = demo.model_copy(update={"monthly_results": (month,)})
    argentina = analytics._trade_series(first, Region.ARGENTINA, Commodity.SOYBEANS)
    europe = analytics._trade_series(first, Region.EUROPEAN_UNION, Commodity.SOYBEANS)
    assert argentina == [(month.month, 0.0, 25.0)]
    assert europe == [(month.month, 25.0, 0.0)]
    assert analytics._trade_series(first, Region.ARGENTINA, Commodity.SOYBEAN_MEAL) == [
        (month.month, 0.0, 0.0)
    ]
    combined = analytics._combined_trade_series(
        first,
        Region.ARGENTINA,
        (Commodity.SOYBEANS, Commodity.SOYBEAN_MEAL, Commodity.SOYBEANS),
    )
    assert combined == [(month.month, 0.0, 25.0)]
    regional = analytics._regional_trade_series(
        first, [Region.ARGENTINA, Region.EUROPEAN_UNION], [Commodity.SOYBEANS]
    )
    assert regional == [(month.month, 25.0, 25.0, 25.0)]


def test_multi_region_chart_series(
    monkeypatch: pytest.MonkeyPatch, demo: SimulationResult
) -> None:
    """Retain independent prices, inventories, and crushing for each region."""
    ui = MagicMock()
    monkeypatch.setattr(analytics, "st", ui)
    regions = [Region.ARGENTINA, Region.BRAZIL]
    analytics._crushing_regions(demo, regions)
    frame = ui.altair_chart.call_args.args[0].data
    assert list(frame["Series"].unique()) == ["Argentina", "Brazil"]
    assert frame.loc[frame["Series"] == "Argentina", "Value"].iloc[0] > 0
    assert frame.loc[frame["Series"] == "Brazil", "Value"].iloc[0] == 0
    prices = {
        f"{analytics._label(region)} / Soybeans": analytics._indexed(
            analytics._price_series(demo, region, Commodity.SOYBEANS)
        )
        for region in regions
    }
    assert all(series[0][1] == 100 for series in prices.values())
    analytics._multi_chart("Prices", prices, "Index")
    assert list(ui.altair_chart.call_args.args[0].data["Series"].unique()) == list(
        prices
    )


def test_indexing_preserves_missing_values_and_zero_baseline() -> None:
    """Normalize from the first observed price without inventing missing data."""
    months = [date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1)]
    values = [(months[0], None), (months[1], 20.0), (months[2], 30.0)]
    assert analytics._indexed(values) == [
        (months[0], None),
        (months[1], 100),
        (months[2], 150),
    ]
    assert analytics._indexed([(months[0], 0.0), (months[1], 10.0)]) == [
        (months[0], None),
        (months[1], None),
    ]


def test_missing_observations_are_not_zero(demo: SimulationResult) -> None:
    """Leave absent regional prices and inventories as missing observations."""
    month = demo.monthly_results[0]
    ending = month.ending_state.model_copy(
        update={
            "snapshot": month.ending_state.snapshot.model_copy(
                update={"inventories": ()}
            )
        }
    )
    changed = month.model_copy(update={"regional_prices": (), "ending_state": ending})
    result = demo.model_copy(update={"monthly_results": (changed,)})
    assert analytics._price_series(result, Region.ARGENTINA, Commodity.SOYBEANS) == [
        (month.month, None)
    ]
    assert analytics._inventory_series(
        result, Region.ARGENTINA, Commodity.SOYBEANS
    ) == [(month.month, None)]


def test_crushing_empty_and_positive_months(
    monkeypatch: pytest.MonkeyPatch, demo: SimulationResult
) -> None:
    """Hide all-zero activity while retaining zero months beside actual crushing."""
    ui = MagicMock()
    monkeypatch.setattr(analytics, "st", ui)
    months = [date(2025, 1, 1), date(2025, 2, 1)]
    series = Mock(return_value=[(months[0], 0.0), (months[1], 0.0)])
    monkeypatch.setattr(analytics, "_crushing_series", series)
    analytics._crushing(demo, Region.BRAZIL)
    ui.bar_chart.assert_not_called()
    assert "No soybean crushing" in ui.info.call_args.args[0]
    series.return_value = [(months[0], 0.0), (months[1], 12.0)]
    analytics._crushing(demo, Region.BRAZIL)
    assert ui.bar_chart.call_args.args[0]["Soybean Crushing (t)"].tolist() == [0, 12]


def test_trade_empty_and_positive_months(
    monkeypatch: pytest.MonkeyPatch, demo: SimulationResult
) -> None:
    """Keep zero totals visible and move active monthly details into an expander."""
    ui = MagicMock()
    monkeypatch.setattr(analytics, "st", ui)
    months = [date(2025, 1, 1), date(2025, 2, 1)]
    series = Mock(return_value=[(months[0], 0.0, 0.0), (months[1], 0.0, 0.0)])
    monkeypatch.setattr(analytics, "_trade_series", series)
    analytics._trade(demo, Region.BRAZIL, Commodity.SOYBEANS)
    ui.bar_chart.assert_not_called()
    ui.dataframe.assert_not_called()
    ui.expander.assert_not_called()
    assert "Imports: 0.00 t · Exports: 0.00 t · Net imports: 0.00 t" in str(
        ui.caption.call_args_list
    )
    series.return_value = [(months[0], 0.0, 0.0), (months[1], 8.0, 3.0)]
    analytics._trade(demo, Region.BRAZIL, Commodity.SOYBEANS)
    frame = ui.bar_chart.call_args.args[0]
    assert frame["Imports (t)"].tolist() == [0.0, 8.0]
    assert frame["Exports (t)"].tolist() == [0.0, 3.0]
    assert frame["Net imports (t)"].tolist() == [0.0, 5.0]
    ui.expander.assert_called_once_with("Monthly trade details")
    assert "Imports: 8.00 t · Exports: 3.00 t · Net imports: 5.00 t" in str(
        ui.caption.call_args_list
    )


def test_missing_activity_is_not_treated_as_zero(
    monkeypatch: pytest.MonkeyPatch, demo: SimulationResult
) -> None:
    """Report incomplete records without claiming that activity was zero."""
    ui = MagicMock()
    monkeypatch.setattr(analytics, "st", ui)
    month = date(2025, 1, 1)
    monkeypatch.setattr(analytics, "_crushing_series", lambda *_: [(month, None)])
    monkeypatch.setattr(analytics, "_trade_series", lambda *_: [(month, None, 0.0)])
    analytics._crushing(demo, Region.BRAZIL)
    analytics._trade(demo, Region.BRAZIL, Commodity.SOYBEANS)
    assert ui.bar_chart.call_count == 2
    assert not any(
        "No soybean crushing" in str(call) for call in ui.info.call_args_list
    )
    assert not any(
        "No interregional trades" in str(call) for call in ui.info.call_args_list
    )
    assert any("unavailable" in str(call) for call in ui.info.call_args_list)


def test_zero_inventory_remains_a_valid_chart(monkeypatch: pytest.MonkeyPatch) -> None:
    """Show an observed zero stock rather than reporting a missing observation."""
    ui = MagicMock()
    monkeypatch.setattr(analytics, "st", ui)
    analytics._line_section(
        "Inventory Evolution", [(date(2025, 1, 1), 0.0)], "Ending inventory (t)"
    )
    assert ui.line_chart.call_args.args[0]["Ending inventory (t)"].tolist() == [0.0]
    ui.info.assert_not_called()


def test_interactive_filters_reuse_unmodified_result(
    monkeypatch: pytest.MonkeyPatch, demo: SimulationResult
) -> None:
    """Change filters and navigate without rerunning or mutating the simulation."""
    load = Mock(wraps=data.run_dashboard_simulation)
    monkeypatch.setattr(data, "run_dashboard_simulation", load)
    before = demo.model_dump_json()
    app = AppTest.from_file(
        Path(__file__).resolve().parents[3] / "dashboard/app.py"
    ).run()
    app.button[0].click().run(timeout=30)
    app.sidebar.radio[0].set_value("Market Analytics").run()
    assert not app.exception and len(app.main.multiselect) == 2
    specifications = [
        json.loads(chart.proto.spec) for chart in app.get("vega_lite_chart")
    ]
    assert [spec["height"] for spec in specifications] == [400, 320, 350, 320]
    assert all(spec["autosize"]["type"] == "fit-x" for spec in specifications)
    assert len(app.main.multiselect[1].value) == 3
    assert {item.value for item in app.subheader} >= {
        "Regional Price Trends",
        "Monthly Crushing",
        "Inventory Evolution",
        "Interregional Trade",
    }
    app.main.multiselect[1].set_value([Commodity.SOYBEANS]).run()
    assert app.expander[0].label == "Monthly trade details"
    assert len(app.dataframe[0].value) == 12
    app.main.multiselect[0].set_value([Region.BRAZIL]).run()
    app.main.multiselect[1].set_value([Commodity.SOYBEAN_OIL]).run()
    assert not app.exception
    assert app.main.multiselect[0].value == [Region.BRAZIL]
    assert app.main.multiselect[1].value == [Commodity.SOYBEAN_OIL]
    assert any("No soybean crushing" in item.value for item in app.info)
    assert any("No interregional trades" in item.value for item in app.info)
    assert not app.main.expander
    app.main.multiselect[0].set_value([Region.EUROPEAN_UNION]).run()
    app.main.multiselect[1].set_value(
        [Commodity.SOYBEAN_MEAL, Commodity.SOYBEAN_OIL]
    ).run()
    app.main.radio[0].set_value("Indexed (first observation = 100)").run()
    assert not app.exception
    app.main.multiselect[0].set_value(list(Region)).run()
    assert not app.exception and len(app.main.multiselect[0].value) == 4
    app.main.multiselect[0].set_value([]).run()
    assert any("Select at least one region" in item.value for item in app.info)
    app.main.multiselect[0].set_value([Region.EUROPEAN_UNION]).run()
    app.main.multiselect[1].set_value([]).run()
    assert any("Select at least one commodity" in item.value for item in app.info)
    app.sidebar.radio[0].set_value("Overview").run()
    assert app.metric[0].value == "960"
    load.assert_called_once()
    assert demo.model_dump_json() == before


def test_chart_months_gaps_and_legend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep zeros and gaps while formatting months across calendar years."""
    ui = MagicMock()
    monkeypatch.setattr(analytics, "st", ui)
    months = [date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]
    frame = pd.DataFrame({"EU / Soybean Meal": [0.0, None, 20.0]}, index=months)
    frame.index.name = "Month"
    before = frame.copy(deep=True)
    analytics._chart(frame, "Ending inventory (t)")
    chart = ui.altair_chart.call_args.args[0]
    spec = chart.to_dict()
    assert spec["encoding"]["x"]["axis"]["format"] == "%b %Y"
    assert spec["encoding"]["color"]["legend"]["orient"] == "bottom"
    assert spec["mark"]["invalid"] == "break-paths-show-domains"
    assert chart.data["Month"].tolist() == months
    assert chart.data["Value"].iloc[0] == 0
    assert pd.isna(chart.data["Value"].iloc[1])
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize(
    "region_count,commodity_count", [(1, 3), (2, 1), (4, 1), (4, 3)]
)
@pytest.mark.parametrize("indexed", [False, True])
def test_price_plot_layout_preserves_selected_series(
    monkeypatch: pytest.MonkeyPatch,
    demo: SimulationResult,
    region_count: int,
    commodity_count: int,
    indexed: bool,
) -> None:
    """Keep plot height independent of legend size in both price display modes."""
    ui = MagicMock()
    monkeypatch.setattr(analytics, "st", ui)
    before = demo.model_dump_json()
    series = {
        f"{region.value} / {commodity.value}": analytics._price_series(
            demo, region, commodity
        )
        for region in list(Region)[:region_count]
        for commodity in list(Commodity)[:commodity_count]
    }
    if indexed:
        series = {label: analytics._indexed(values) for label, values in series.items()}
    unit = (
        "Price index (first observation = 100)" if indexed else "Observed price (USD/t)"
    )
    analytics._multi_chart("Prices", series, unit)
    chart = ui.altair_chart.call_args.args[0]
    spec = chart.to_dict()
    assert spec["height"] == 400
    assert spec["autosize"] == {"type": "fit-x", "contains": "padding"}
    assert "width" not in spec
    legend = spec["encoding"]["color"]["legend"]
    assert legend["columns"] == 2 and legend["direction"] == "horizontal"
    assert legend["orient"] == "bottom"
    assert spec["encoding"]["y"]["title"] == unit
    assert {item["field"] for item in spec["encoding"]["tooltip"]} >= {
        "Series",
        "Value",
    }
    assert ui.altair_chart.call_args.kwargs == {"width": "stretch", "height": "content"}
    assert list(chart.data["Series"].unique()) == list(series)
    for label, values in series.items():
        observed = chart.data.loc[chart.data["Series"] == label]
        assert observed["Month"].tolist() == [month for month, _ in values]
        assert observed["Value"].tolist() == [value for _, value in values]
        if indexed:
            assert observed["Value"].iloc[0] == 100
    assert demo.model_dump_json() == before
