"""Transform regional Crusher capacity and cost without changing market inputs."""

from collections.abc import Mapping
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from soybean_sim.agents import CrusherConfig
from soybean_sim.models import Region


class CrusherParameterShockConfig(BaseModel):
    """Set finite changes up to +1000%; capacity must remain strictly positive."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    region: Region
    capacity_change_percent: Annotated[
        float, Field(strict=True, gt=-100, le=1000, allow_inf_nan=False)
    ] = 0.0
    processing_cost_change_percent: Annotated[
        float, Field(strict=True, ge=-100, le=1000, allow_inf_nan=False)
    ] = 0.0


def apply_crusher_parameter_shock(
    crusher_configs: Mapping[Region, CrusherConfig],
    *,
    config: CrusherParameterShockConfig,
) -> dict[Region, CrusherConfig]:
    """Return independent validated configurations, changing only capacity and cost."""
    if not isinstance(config, CrusherParameterShockConfig):
        raise ValueError("config must be a CrusherParameterShockConfig")
    settings = CrusherParameterShockConfig.model_validate(
        config.model_dump(warnings=False)
    )
    if settings.region not in crusher_configs:
        raise ValueError(f"No Crusher configuration for {settings.region.value}")
    result = {}
    for region, original in crusher_configs.items():
        values = CrusherConfig.model_validate(
            original.model_dump(warnings=False)
        ).model_dump()
        if region == settings.region:
            values["monthly_capacity_tonnes"] *= (
                1 + settings.capacity_change_percent / 100
            )
            values["processing_cost_usd_per_tonne"] *= (
                1 + settings.processing_cost_change_percent / 100
            )
        result[Region(region)] = CrusherConfig.model_validate(values)
    return result
