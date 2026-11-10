"""Request soybean processing from reference-price margins and regional stock bounds."""

import math
from dataclasses import dataclass
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soybean_sim.models import Commodity, MarketSnapshot

from .models import AgentDecision, AgentIdentity, AgentType, DecisionAction

_YIELD_SUM_TOLERANCE: Final = 1e-9
_PositiveFinite = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
_NonNegativeFinite = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
_Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
_UtilizationFraction = Annotated[
    float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)
]


class CrusherConfig(BaseModel):
    """Set input capacity, processing costs, simplified yields, and margin bands."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    monthly_capacity_tonnes: _PositiveFinite
    processing_cost_usd_per_tonne: _NonNegativeFinite
    meal_yield_fraction: _NonNegativeFinite = 0.80
    oil_yield_fraction: _NonNegativeFinite = 0.20
    low_margin_threshold_usd_per_tonne: _Finite = 0
    high_margin_threshold_usd_per_tonne: _Finite = 30
    full_utilization_threshold_usd_per_tonne: _Finite = 60
    low_utilization_fraction: _UtilizationFraction = 0.30
    medium_utilization_fraction: _UtilizationFraction = 0.60
    high_utilization_fraction: _UtilizationFraction = 1.00

    @model_validator(mode="after")
    def yields_conserve_input(self) -> Self:
        """Require meal and oil yields to sum to one within float tolerance."""
        if not math.isclose(
            self.meal_yield_fraction + self.oil_yield_fraction,
            1,
            rel_tol=0,
            abs_tol=_YIELD_SUM_TOLERANCE,
        ):
            raise ValueError(
                "meal and oil yield fractions must sum to 1 within "
                f"{_YIELD_SUM_TOLERANCE:g}"
            )

        return self

    @model_validator(mode="after")
    def margin_thresholds_are_ordered(self) -> Self:
        """Require strictly increasing margin thresholds in USD per input tonne."""
        if not (
            self.low_margin_threshold_usd_per_tonne
            < self.high_margin_threshold_usd_per_tonne
            < self.full_utilization_threshold_usd_per_tonne
        ):
            raise ValueError("margin thresholds must be strictly increasing")

        return self

    @model_validator(mode="after")
    def utilization_fractions_are_ordered(self) -> Self:
        """Require capacity use to be nondecreasing across active margin bands."""
        if not (
            self.low_utilization_fraction
            <= self.medium_utilization_fraction
            <= self.high_utilization_fraction
        ):
            raise ValueError("utilization fractions must be nondecreasing")

        return self


class CrusherDecisionError(ValueError):
    """Crusher observations are missing or its computed margin is not finite."""


@dataclass(frozen=True)
class Crusher:
    """Represent aggregate regional processing capacity without owning shared stocks."""

    identity: AgentIdentity
    config: CrusherConfig

    def __post_init__(self) -> None:
        """Require the supplied identity to identify a crusher agent."""
        if self.identity.agent_type != AgentType.CRUSHER:
            raise ValueError("Crusher identity must have agent_type='crusher'")

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Request soybean input within capacity and regional stock bounds."""
        net_margin = self._net_crush_margin(snapshot)
        regional_stock = self._regional_soybean_inventory(snapshot)
        desired_tonnes = self.config.monthly_capacity_tonnes * self._utilization(
            net_margin
        )

        requested_tonnes = min(desired_tonnes, regional_stock)
        if requested_tonnes == 0:
            return ()

        return (
            AgentDecision(
                agent=self.identity,
                month=snapshot.month,
                action=DecisionAction.CRUSH,
                commodity=Commodity.SOYBEANS,
                quantity_tonnes=requested_tonnes,
            ),
        )

    def _commodity_price(self, snapshot: MarketSnapshot, commodity: Commodity) -> float:
        """Read a required global reference price or identify missing data."""
        for price in snapshot.prices:
            if price.commodity == commodity:
                return price.price_usd_per_tonne
        raise CrusherDecisionError(
            f"Crusher {self.identity.agent_id!r}: missing global price for "
            f"{commodity.value} in {snapshot.month.isoformat()}"
        )

    def _net_crush_margin(self, snapshot: MarketSnapshot) -> float:
        """Subtract soybean input and processing costs from weighted product revenue."""
        soybean_price = self._commodity_price(snapshot, Commodity.SOYBEANS)
        meal_price = self._commodity_price(snapshot, Commodity.SOYBEAN_MEAL)
        oil_price = self._commodity_price(snapshot, Commodity.SOYBEAN_OIL)
        gross_crush_spread = (
            self.config.meal_yield_fraction * meal_price
            + self.config.oil_yield_fraction * oil_price
            - soybean_price
        )

        net_margin = gross_crush_spread - self.config.processing_cost_usd_per_tonne
        if not math.isfinite(net_margin):
            raise CrusherDecisionError(
                f"Crusher {self.identity.agent_id!r}: nonfinite crush margin "
                f"for {snapshot.month.isoformat()}"
            )

        return net_margin

    def _regional_soybean_inventory(self, snapshot: MarketSnapshot) -> float:
        """Read regional soybean tonnes as a shared request ceiling."""
        for stock in snapshot.inventories:
            if (
                stock.region == self.identity.region
                and stock.commodity == Commodity.SOYBEANS
            ):
                return stock.quantity_tonnes
        raise CrusherDecisionError(
            f"Crusher {self.identity.agent_id!r}: missing soybean inventory "
            f"for {self.identity.region.value} in {snapshot.month.isoformat()}"
        )

    def _utilization(self, net_margin: float) -> float:
        """Choose capacity use from configured margin bands with explicit boundaries."""
        if net_margin <= self.config.low_margin_threshold_usd_per_tonne:
            return 0

        if net_margin < self.config.high_margin_threshold_usd_per_tonne:
            return self.config.low_utilization_fraction

        if net_margin < self.config.full_utilization_threshold_usd_per_tonne:
            return self.config.medium_utilization_fraction

        return self.config.high_utilization_fraction
