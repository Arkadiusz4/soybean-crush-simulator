import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

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
    with patch.dict(
        sys.modules,
        {
            specification.name: module,
            report_specification.name: report,
        },
    ):
        report_specification.loader.exec_module(report)
        specification.loader.exec_module(module)
    return module
