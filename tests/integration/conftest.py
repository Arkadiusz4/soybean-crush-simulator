import csv
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from openpyxl import Workbook


@pytest.fixture(scope="session")
def market_smoke() -> ModuleType:
    """Load the standalone smoke script without changing package paths or installation.

    Returns:
        ModuleType: Script exposing shared synthetic setup and independent assertions.
    """
    path = Path(__file__).resolve().parents[2] / "tests/support/smoke_test_market.py"
    specification = importlib.util.spec_from_file_location("market_smoke", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    report_specification = importlib.util.spec_from_file_location(
        "market_smoke_report", path.with_name("market_smoke_report.py")
    )
    assert report_specification is not None and report_specification.loader is not None
    report = importlib.util.module_from_spec(report_specification)
    with pytest.MonkeyPatch.context() as registration:
        registration.setitem(sys.modules, specification.name, module)
        registration.setitem(sys.modules, report_specification.name, report)
        report_specification.loader.exec_module(report)
        specification.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def scenario_demo(market_smoke: ModuleType) -> ModuleType:
    """Load the paired demo while reusing the existing smoke accounting/report helpers.

    Args:
        market_smoke (ModuleType): Previously loaded physical accounting helpers.

    Returns:
        ModuleType: Standalone synthetic comparison script and Rich presentation.
    """
    directory = Path(__file__).resolve().parents[2] / "tests/support"
    modules = {
        "market_smoke_report": market_smoke.report,
        "smoke_test_market": market_smoke,
    }
    with pytest.MonkeyPatch.context() as registration:
        for name, dependency in modules.items():
            registration.setitem(sys.modules, name, dependency)
        for name in ("scenario_comparison_report", "compare_scenarios"):
            specification = importlib.util.spec_from_file_location(
                name, directory / f"{name}.py"
            )
            assert specification is not None and specification.loader is not None
            module = importlib.util.module_from_spec(specification)
            registration.setitem(sys.modules, name, module)
            specification.loader.exec_module(module)
        return module


@pytest.fixture(scope="session")
def historical_script(
    market_smoke: ModuleType, scenario_demo: ModuleType
) -> ModuleType:
    """Load the historical command with its existing presentation/accounting helpers.

    Args:
        market_smoke (ModuleType): Physical accounting assertions and tables.
        scenario_demo (ModuleType): Existing paired-comparison presentation.

    Returns:
        ModuleType: Historical script callable without altering installation paths.
    """
    directory = Path(__file__).resolve().parents[2] / "tests/support"
    dependencies = {
        "market_smoke_report": market_smoke.report,
        "smoke_test_market": market_smoke,
        "scenario_comparison_report": scenario_demo.report,
    }
    with pytest.MonkeyPatch.context() as registration:
        for name, dependency in dependencies.items():
            registration.setitem(sys.modules, name, dependency)
        for name in ("historical_market_report", "smoke_test_historical_market"):
            specification = importlib.util.spec_from_file_location(
                name, directory / f"{name}.py"
            )
            assert specification is not None and specification.loader is not None
            module = importlib.util.module_from_spec(specification)
            registration.setitem(sys.modules, name, module)
            specification.loader.exec_module(module)
        return module


@pytest.fixture
def historical_paths(tmp_path: Path) -> tuple[Path, Path]:
    """Write explicitly synthetic observations in the verified USDA/Pink Sheet layouts.

    Args:
        tmp_path (Path): Isolated local fixture directory.

    Returns:
        tuple[Path, Path]: Small CSV and workbook accepted by the real loaders.
    """
    csv_path = tmp_path / "synthetic_usda.csv"
    columns = (
        "Commodity_Code",
        "Commodity_Description",
        "Country_Code",
        "Country_Name",
        "Market_Year",
        "Attribute_ID",
        "Attribute_Description",
        "Unit_ID",
        "Unit_Description",
        "Value",
    )
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        for country, name in (
            ("AR", "Argentina"),
            ("BR", "Brazil"),
            ("E4", "European Union"),
            ("US", "United States"),
        ):
            for code, description, consumption in (
                ("2222000", "Oilseed, Soybean", "0.012"),
                ("0813100", "Meal, Soybean", "0.024"),
                ("4232000", "Oil, Soybean", "0.012"),
            ):
                for attribute, label, value in (
                    (
                        "028",
                        "Production",
                        "1.2"
                        if code == "2222000"
                        else "0.096"
                        if code == "0813100"
                        else "0.024",
                    ),
                    ("020", "Beginning Stocks", "0.1"),
                    ("125", "Domestic Consumption", consumption),
                    ("057", "Imports", "0"),
                    ("088", "Exports", "0"),
                ):
                    writer.writerow(
                        (
                            code,
                            description,
                            country,
                            name,
                            "2024",
                            attribute,
                            label,
                            "08",
                            "(1000 MT)",
                            value,
                        )
                    )
                if code == "2222000":
                    writer.writerow(
                        (
                            code,
                            description,
                            country,
                            name,
                            "2024",
                            "007",
                            "Crush",
                            "08",
                            "(1000 MT)",
                            "0.12",
                        )
                    )
    workbook_path = tmp_path / "synthetic_pink_sheet.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Monthly Prices"
    for row in (
        ("Synthetic fixture, not historical observations",),
        (),
        (),
        (),
        ("Month", "Soybeans", "Soybean meal", "Soybean oil"),
        (None, "($/mt)", "($/mt)", "($/mt)"),
        ("2025M01", 100, 200, 300),
    ):
        sheet.append(row)
    workbook.save(workbook_path)
    workbook.close()
    return csv_path, workbook_path
