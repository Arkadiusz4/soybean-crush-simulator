"""Configuration bounds, type safety, and useful file validation failures."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from soybean_sim.config import ConfigurationError, SimulationConfig, load_config
from soybean_sim.models import Region


def test_valid_config_converts_regions_and_trims_name(
    config_data: dict[str, Any],
) -> None:
    config_data["scenario_name"] = " baseline "
    config = SimulationConfig.model_validate(config_data)

    assert config.duration_steps == 12
    assert config.random_seed == 42
    assert config.regions == (Region.UNITED_STATES, Region.BRAZIL)
    assert config.scenario_name == "baseline"


def test_config_accepts_smallest_duration_and_zero_seed(
    config_data: dict[str, Any],
) -> None:
    config_data.update(duration_steps=1, random_seed=0)
    config = SimulationConfig.model_validate(config_data)
    assert config.duration_steps == 1
    assert config.random_seed == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("duration_steps", 0),
        ("duration_steps", -1),
        ("duration_steps", True),
        ("duration_steps", "12"),
        ("duration_steps", 12.0),
        ("random_seed", -1),
        ("random_seed", False),
        ("random_seed", "42"),
        ("random_seed", 42.0),
        ("regions", []),
        ("regions", ["brazil", "brazil"]),
        ("regions", ["unknown"]),
        ("regions", "brazil"),
        ("scenario_name", ""),
        ("scenario_name", "  "),
        ("scenario_name", 123),
        ("scenario_name", None),
        ("unexpected_field", "typo"),
    ],
)
def test_invalid_fields_are_rejected(
    config_data: dict[str, Any], field: str, value: Any
) -> None:
    config_data[field] = value
    with pytest.raises(ValidationError) as caught:
        SimulationConfig.model_validate(config_data)
    assert caught.value.errors()[0]["loc"][0] == field


@pytest.mark.parametrize(
    "field", ["duration_steps", "random_seed", "regions", "scenario_name"]
)
def test_all_settings_are_explicit(config_data: dict[str, Any], field: str) -> None:
    del config_data[field]
    with pytest.raises(ValidationError, match=field):
        SimulationConfig.model_validate(config_data)


def test_configuration_cannot_be_reassigned(config_data: dict[str, Any]) -> None:
    config = SimulationConfig.model_validate(config_data)
    with pytest.raises(ValidationError, match="frozen"):
        config.random_seed = 7


def test_sample_config(baseline_path: Path) -> None:
    config = load_config(baseline_path)
    assert config.duration_steps == 120
    assert config.random_seed == 42
    assert config.regions == tuple(Region)
    assert config.scenario_name == "baseline"


@pytest.mark.parametrize("contents", ["", "null", "[]", "- brazil", "42", "baseline"])
def test_yaml_root_must_be_mapping(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="expected a YAML mapping"):
        load_config(path)


def test_malformed_yaml_has_file_and_parse_context(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("regions: [brazil", encoding="utf-8")
    with pytest.raises(ConfigurationError) as caught:
        load_config(path)
    assert str(path) in str(caught.value)
    assert "Invalid YAML" in str(caught.value)
    assert "line" in str(caught.value)


def test_unsafe_yaml_tags_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.yaml"
    path.write_text("!!python/tuple [1, 2]", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Invalid YAML"):
        load_config(path)


@pytest.mark.parametrize("kind", ["missing", "directory", "non_utf8"])
def test_unreadable_files_are_explained(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "config.yaml"
    if kind == "directory":
        path.mkdir()
    elif kind == "non_utf8":
        path.write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigurationError) as caught:
        load_config(path)
    assert "Cannot read" in str(caught.value)
    assert str(path) in str(caught.value)


def test_validation_reports_all_fields_with_locations(
    tmp_path: Path, config_data: dict[str, Any]
) -> None:
    config_data.update(duration_steps=0, regions=["unknown"])
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    with pytest.raises(ConfigurationError) as caught:
        load_config(str(path))
    message = str(caught.value)
    assert str(path) in message
    assert "duration_steps:" in message
    assert "regions.0:" in message
