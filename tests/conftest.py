from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def config_data() -> dict[str, Any]:
    """Return fresh valid run settings so tests can change fields independently."""
    return {
        "duration_steps": 12,
        "random_seed": 42,
        "regions": ["united_states", "brazil"],
        "scenario_name": "baseline",
    }


@pytest.fixture
def baseline_path() -> Path:
    """Locate the checked-in baseline YAML independently of the working directory."""
    return Path(__file__).resolve().parents[1] / "configs" / "baseline.yaml"
