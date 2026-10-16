"""Shared configuration fixtures."""

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def config_data() -> dict[str, Any]:
    return {
        "duration_steps": 12,
        "random_seed": 42,
        "regions": ["united_states", "brazil"],
        "scenario_name": "baseline",
    }


@pytest.fixture
def baseline_path() -> Path:
    return Path(__file__).resolve().parents[1] / "configs" / "baseline.yaml"
