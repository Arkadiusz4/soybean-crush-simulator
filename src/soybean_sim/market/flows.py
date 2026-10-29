"""Allocate annual USDA quantities to assumed monthly flows without applying them."""

import math
from collections.abc import Iterable
from datetime import date
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from soybean_sim.models import Commodity, MarketBalance, Region

from .contracts import MarketFlow, MarketFlowType

_MONTHS_PER_YEAR: Final = 12
_WEIGHT_SUM_TOLERANCE: Final = 1e-9
_UNIFORM_WEIGHTS: Final = (1 / _MONTHS_PER_YEAR,) * _MONTHS_PER_YEAR
_Weight = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
_MonthlyWeights = Annotated[
    tuple[_Weight, ...],
    Field(min_length=_MONTHS_PER_YEAR, max_length=_MONTHS_PER_YEAR),
]
_BalanceKey = tuple[Region, Commodity, str]


class MonthlyFlowConfig(BaseModel):
    """Set January-to-December allocation weights, defaulting to uniform shares.

    Weights are modeling assumptions, not observed monthly USDA quantities or
    region-specific harvest calendars. Each series sums to one within 1e-9.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    supply_weights: _MonthlyWeights = _UNIFORM_WEIGHTS
    consumption_weights: _MonthlyWeights = _UNIFORM_WEIGHTS

    @field_validator("supply_weights", "consumption_weights")
    @classmethod
    def weights_sum_to_one(cls, weights: tuple[float, ...]) -> tuple[float, ...]:
        """Require a full annual allocation, allowing only rounding-scale residuals."""
        if not math.isclose(sum(weights), 1, rel_tol=0, abs_tol=_WEIGHT_SUM_TOLERANCE):
            raise ValueError(
                f"monthly weights must sum to 1 within {_WEIGHT_SUM_TOLERANCE:g}"
            )

        return weights


class MarketFlowError(ValueError):
    """Flow inputs have invalid periods, quantities, weights, or conflicting records."""


def generate_monthly_flows(
    balances: Iterable[MarketBalance],
    *,
    month: date,
    config: MonthlyFlowConfig,
) -> tuple[MarketFlow, ...]:
    """Allocate one marketing year's balances using the calendar month's weights.

    Return flows sorted by region, commodity, and type. Missing annual values are
    skipped; explicit zeros remain. Soybean domestic consumption is excluded to
    avoid counting crushing inputs twice. No non-crush consumption is inferred.
    The source year is not aligned to the calendar month; callers select it.
    Empty input returns an empty tuple, and source balances remain unchanged.
    """
    validated_config = _validate_request(month, config)
    selected_balances = _unique_balances(balances)
    flows = [
        flow
        for balance in selected_balances
        for flow in _balance_flows(balance, month, validated_config)
    ]

    return tuple(
        sorted(flows, key=lambda flow: (flow.region, flow.commodity, flow.flow_type))
    )


def _validate_request(month: date, config: MonthlyFlowConfig) -> MonthlyFlowConfig:
    """Require an explicit calendar month and revalidate supplied allocation weights."""
    if type(month) is not date or month.day != 1:
        raise MarketFlowError("month must be a date on the first day of a month")

    if not isinstance(config, MonthlyFlowConfig):
        raise MarketFlowError("config must be a MonthlyFlowConfig")
    try:
        return MonthlyFlowConfig.model_validate(config.model_dump())
    except ValidationError as exc:
        raise MarketFlowError(f"Invalid monthly flow configuration: {exc}") from exc


def _unique_balances(balances: Iterable[MarketBalance]) -> tuple[MarketBalance, ...]:
    """Validate annual records, collapse equal duplicates, and require a single year."""
    selected: dict[_BalanceKey, MarketBalance] = {}
    years: set[str] = set()

    for balance in balances:
        if not isinstance(balance, MarketBalance):
            raise MarketFlowError("balances must contain MarketBalance records")
        try:
            validated = MarketBalance.model_validate(balance.model_dump())
        except ValidationError as exc:
            raise MarketFlowError(f"Invalid annual market balance: {exc}") from exc

        key = (validated.region, validated.commodity, validated.marketing_year)
        previous = selected.get(key)
        if previous is not None and previous != validated:
            raise MarketFlowError(
                f"Conflicting balances for {validated.region.value}/"
                f"{validated.commodity.value} in marketing year "
                f"{validated.marketing_year!r}"
            )

        selected[key] = validated
        years.add(validated.marketing_year)

    if len(years) > 1:
        raise MarketFlowError(
            f"Mixed marketing years are not supported: {', '.join(sorted(years))}"
        )

    return tuple(selected.values())


def _balance_flows(
    balance: MarketBalance, month: date, config: MonthlyFlowConfig
) -> tuple[MarketFlow, ...]:
    """Allocate observed production and eligible consumption, validating the results."""
    month_index = month.month - 1
    allocations = [
        (
            MarketFlowType.SUPPLY,
            balance.production_tonnes,
            config.supply_weights[month_index],
        )
    ]

    if balance.commodity != Commodity.SOYBEANS:
        allocations.append(
            (
                MarketFlowType.CONSUMPTION,
                balance.domestic_consumption_tonnes,
                config.consumption_weights[month_index],
            )
        )

    flows: list[MarketFlow] = []
    for flow_type, annual_quantity, weight in allocations:
        if annual_quantity is None:
            continue
        try:
            flows.append(
                MarketFlow(
                    month=month,
                    region=balance.region,
                    commodity=balance.commodity,
                    flow_type=flow_type,
                    quantity_tonnes=annual_quantity * weight,
                )
            )
        except ValidationError as exc:
            raise MarketFlowError(
                f"Invalid monthly {flow_type.value} for {balance.region.value}/"
                f"{balance.commodity.value} in {month.isoformat()}: {exc}"
            ) from exc

    return tuple(flows)
