"""Configuration schema and YAML loading, independent of presentation layers."""

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

from soybean_sim.models import Region


class ConfigurationError(ValueError):
    """A configuration file could not be read or failed validation."""


class SimulationConfig(BaseModel):
    """Validated run settings; no economic behavior is implied by these fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    duration_steps: Annotated[int, Field(strict=True, gt=0)]
    random_seed: Annotated[int, Field(strict=True, ge=0)]
    regions: Annotated[tuple[Region, ...], Field(min_length=1)]
    scenario_name: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
    ]

    @field_validator("regions")
    @classmethod
    def regions_are_unique(cls, regions: tuple[Region, ...]) -> tuple[Region, ...]:
        """Reject accidental double counting of a selected region."""
        if len(regions) != len(set(regions)):
            raise ValueError("regions must not contain duplicates")
        return regions


def load_config(path: str | Path) -> SimulationConfig:
    """Read a UTF-8 YAML mapping and report file or field errors with context."""
    config_path = Path(path)
    try:
        contents = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError(f"Cannot read '{config_path}': {exc}") from exc

    try:
        data = yaml.safe_load(contents)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in '{config_path}': {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigurationError(
            f"Invalid configuration in '{config_path}': expected a YAML mapping"
        )

    try:
        return SimulationConfig.model_validate(data)
    except ValidationError as exc:
        details = "\n".join(
            f"  {'.'.join(map(str, error['loc']))}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigurationError(
            f"Invalid configuration in '{config_path}':\n{details}"
        ) from exc
