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
    """Immutable run settings with a positive duration and unique regions.

    All settings are required. The scenario name is a label, not a runnable scenario.
    """

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
        """Check that each selected region appears only once."""
        if len(regions) != len(set(regions)):
            raise ValueError("regions must not contain duplicates")
        return regions


def load_config(path: str | Path) -> SimulationConfig:
    """Load a local UTF-8 YAML mapping into validated, immutable run settings."""
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
