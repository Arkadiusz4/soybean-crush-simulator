"""Reduce Argentina's new soybean supply without changing other market inputs."""

from collections.abc import Iterable
from datetime import date
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from soybean_sim.market import MarketFlow, MarketFlowType
from soybean_sim.models import Commodity, Region

_FlowKey = tuple[date, Region, Commodity, MarketFlowType]


class ScenarioError(ValueError):
    """Invalid scenario configuration or market flow input."""


class ArgentinaDroughtConfig(BaseModel):
    """Set a synthetic production loss and optional inclusive calendar-month bounds.

    Args:
        production_reduction_fraction (float): Finite share of new soybean supply
            lost, from zero to one; the default is an assumption, not historical data.
        start_month (date | None): Inclusive first month, or None for no lower bound.
        end_month (date | None): Inclusive last month, or None for no upper bound.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    production_reduction_fraction: Annotated[
        float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)
    ] = Field(default=0.30, description="Fraction of Argentina soybean supply lost.")
    start_month: Annotated[date, Field(strict=True)] | None = Field(
        default=None, description="Inclusive first calendar month; None is unbounded."
    )
    end_month: Annotated[date, Field(strict=True)] | None = Field(
        default=None, description="Inclusive last calendar month; None is unbounded."
    )

    @field_validator("start_month", "end_month")
    @classmethod
    def bounds_start_on_first_day(cls, month: date | None) -> date | None:
        """Require explicit bounds to identify whole calendar months.

        Args:
            month (date | None): Optional bound to validate.

        Returns:
            date | None: Unchanged first-of-month date or unbounded None.
        """
        if month is not None and month.day != 1:
            raise ValueError("drought bounds must be the first day of a month")
        return month

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> Self:
        """Allow single-month intervals and reject reversed date bounds.

        Returns:
            Self: Validated drought configuration.
        """
        if (
            self.start_month is not None
            and self.end_month is not None
            and self.start_month > self.end_month
        ):
            raise ValueError("start_month must not be after end_month")
        return self


def apply_argentina_drought(
    flows: Iterable[MarketFlow], *, config: ArgentinaDroughtConfig
) -> tuple[MarketFlow, ...]:
    """Reduce eligible soybean supply after validating the complete input collection.

    Args:
        flows (Iterable[MarketFlow]): Baseline monthly records in metric tonnes.
        config (ArgentinaDroughtConfig): Production loss and inclusive date bounds.

    Returns:
        tuple[MarketFlow, ...]: Validated flows sorted by month, region, commodity,
            and flow type; baseline records remain unchanged.
    """
    validated_config = _validate_config(config)
    baseline = _validated_flows(flows)
    return tuple(_adjust_flow(flow, validated_config) for flow in baseline)


def _validate_config(config: ArgentinaDroughtConfig) -> ArgentinaDroughtConfig:
    """Revalidate settings, including copies that bypassed Pydantic validation.

    Args:
        config (ArgentinaDroughtConfig): Candidate reduction and date interval.

    Returns:
        ArgentinaDroughtConfig: Revalidated settings with scenario error context.
    """
    if not isinstance(config, ArgentinaDroughtConfig):
        raise ScenarioError("config must be an ArgentinaDroughtConfig")
    try:
        return ArgentinaDroughtConfig.model_validate(config.model_dump(warnings=False))
    except ValidationError as exc:
        raise ScenarioError(f"Invalid drought configuration: {exc}") from exc


def _validated_flows(flows: Iterable[MarketFlow]) -> tuple[MarketFlow, ...]:
    """Check complete monthly records and unique keys before adjusting any quantities.

    Args:
        flows (Iterable[MarketFlow]): Candidate baseline records.

    Returns:
        tuple[MarketFlow, ...]: Sorted, revalidated flows with supported consumption.
    """
    try:
        records = iter(flows)
    except TypeError as exc:
        raise ScenarioError("flows must be an iterable of MarketFlow records") from exc

    indexed: dict[_FlowKey, MarketFlow] = {}
    for position, flow in enumerate(records, start=1):
        if not isinstance(flow, MarketFlow):
            raise ScenarioError(f"Flow {position}: expected a MarketFlow record")
        try:
            validated = MarketFlow.model_validate(flow.model_dump(warnings=False))
        except ValidationError as exc:
            raise ScenarioError(f"Invalid flow {position}: {exc}") from exc
        if (
            validated.commodity == Commodity.SOYBEANS
            and validated.flow_type == MarketFlowType.CONSUMPTION
        ):
            raise ScenarioError(f"Flow {position}: unsupported soybean consumption")
        key = (
            validated.month,
            validated.region,
            validated.commodity,
            validated.flow_type,
        )
        if key in indexed:
            raise ScenarioError(
                f"Duplicate flow for {validated.month}: {validated.region.value}/"
                f"{validated.commodity.value}/{validated.flow_type.value}"
            )
        indexed[key] = validated
    return tuple(indexed[key] for key in sorted(indexed))


def _adjust_flow(flow: MarketFlow, config: ArgentinaDroughtConfig) -> MarketFlow:
    """Construct a validated reduced supply record only inside the drought interval.

    Args:
        flow (MarketFlow): Revalidated baseline observation.
        config (ArgentinaDroughtConfig): Revalidated reduction and date bounds.

    Returns:
        MarketFlow: Adjusted supply or unchanged quantities for an ineligible record.
    """
    if (
        flow.region != Region.ARGENTINA
        or flow.commodity != Commodity.SOYBEANS
        or flow.flow_type != MarketFlowType.SUPPLY
    ):
        return flow
    if config.start_month is not None and flow.month < config.start_month:
        return flow
    if config.end_month is not None and flow.month > config.end_month:
        return flow
    try:
        return MarketFlow(
            month=flow.month,
            region=flow.region,
            commodity=flow.commodity,
            flow_type=flow.flow_type,
            quantity_tonnes=flow.quantity_tonnes
            * (1.0 - config.production_reduction_fraction),
        )
    except ValidationError as exc:
        raise ScenarioError(
            f"Invalid adjusted supply for Argentina in {flow.month}: {exc}"
        ) from exc
