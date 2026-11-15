import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="session")
def market_smoke() -> ModuleType:
    """Load the standalone smoke script without changing package paths or installation.

    Returns:
        ModuleType: Script exposing shared synthetic setup and independent assertions.
    """
    path = Path(__file__).resolve().parents[2] / "scripts/smoke_test_market.py"
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
    directory = Path(__file__).resolve().parents[2] / "scripts"
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
