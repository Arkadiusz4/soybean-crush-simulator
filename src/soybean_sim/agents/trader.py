"""Propose soybean purchases and regional product sales from global price ratios."""

from dataclasses import dataclass
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soybean_sim.models import Commodity, MarketSnapshot, NonNegativeTonnes

from .models import AgentDecision, AgentIdentity, AgentType, DecisionAction

_PositiveFinite = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
_Fraction = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


class TraderConfig(BaseModel):
    """Set USD/tonne baselines, monthly demand limits, and price-response fractions."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    reference_soybean_price_usd_per_tonne: _PositiveFinite
    reference_soybean_meal_price_usd_per_tonne: _PositiveFinite
    reference_soybean_oil_price_usd_per_tonne: _PositiveFinite
    maximum_monthly_purchase_tonnes: NonNegativeTonnes
    low_price_ratio: _PositiveFinite = 0.90
    high_price_ratio: _PositiveFinite = 1.10
    low_purchase_fraction: _Fraction = 1.00
    normal_purchase_fraction: _Fraction = 0.50
    high_purchase_fraction: _Fraction = 0.20
    low_sale_fraction: _Fraction = 0.20
    normal_sale_fraction: _Fraction = 0.50
    high_sale_fraction: _Fraction = 0.80

    @model_validator(mode="after")
    def price_thresholds_are_ordered(self) -> Self:
        """Require strictly increasing price-ratio thresholds."""
        if self.high_price_ratio <= self.low_price_ratio:
            raise ValueError("high_price_ratio must be greater than low_price_ratio")

        return self

    @model_validator(mode="after")
    def purchase_fractions_are_ordered(self) -> Self:
        """Require purchase demand to be nonincreasing as prices rise."""
        if not (
            self.low_purchase_fraction
            >= self.normal_purchase_fraction
            >= self.high_purchase_fraction
        ):
            raise ValueError(
                "purchase fractions must be nonincreasing from low to high"
            )

        return self

    @model_validator(mode="after")
    def sale_fractions_are_ordered(self) -> Self:
        """Require product sale fractions to be nondecreasing as prices rise."""
        if not (
            self.low_sale_fraction
            <= self.normal_sale_fraction
            <= self.high_sale_fraction
        ):
            raise ValueError("sale fractions must be nondecreasing from low to high")

        return self


class TraderDecisionError(ValueError):
    """A required global price or regional product inventory is missing."""


@dataclass(frozen=True)
class Trader:
    """Represent regional trading intentions without ownership or execution."""

    identity: AgentIdentity
    config: TraderConfig

    def __post_init__(self) -> None:
        """Require the supplied identity to identify a trader agent."""
        if self.identity.agent_type != AgentType.TRADER:
            raise ValueError("Trader identity must have agent_type='trader'")

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Propose positive requests sorted by commodity and action identifiers."""
        proposals = (
            self._soybean_purchase(snapshot),
            self._product_sale(
                snapshot,
                Commodity.SOYBEAN_MEAL,
                self.config.reference_soybean_meal_price_usd_per_tonne,
            ),
            self._product_sale(
                snapshot,
                Commodity.SOYBEAN_OIL,
                self.config.reference_soybean_oil_price_usd_per_tonne,
            ),
        )

        return tuple(
            sorted(
                (proposal for proposal in proposals if proposal is not None),
                key=lambda decision: (decision.commodity, decision.action),
            )
        )

    def _soybean_purchase(self, snapshot: MarketSnapshot) -> AgentDecision | None:
        """Request soybean demand independently of current regional soybean stock."""
        market_price = self._commodity_price(snapshot, Commodity.SOYBEANS)
        price_ratio = market_price / self.config.reference_soybean_price_usd_per_tonne
        
        quantity = (
            self.config.maximum_monthly_purchase_tonnes
            * self._purchase_fraction(price_ratio)
        )
        if quantity == 0:
            return None

        return AgentDecision(
            agent=self.identity,
            month=snapshot.month,
            action=DecisionAction.PURCHASE,
            commodity=Commodity.SOYBEANS,
            quantity_tonnes=quantity,
        )

    def _product_sale(
        self, snapshot: MarketSnapshot, commodity: Commodity, reference_price: float
    ) -> AgentDecision | None:
        """Offer a price-dependent share of observed regional product stock."""
        market_price = self._commodity_price(snapshot, commodity)
        regional_stock = self._regional_product_inventory(snapshot, commodity)
        price_ratio = market_price / reference_price

        quantity = regional_stock * self._sale_fraction(price_ratio)
        if quantity == 0:
            return None

        return AgentDecision(
            agent=self.identity,
            month=snapshot.month,
            action=DecisionAction.OFFER_FOR_SALE,
            commodity=commodity,
            quantity_tonnes=quantity,
        )

    def _commodity_price(self, snapshot: MarketSnapshot, commodity: Commodity) -> float:
        """Read a required global reference price or identify missing data."""
        for price in snapshot.prices:
            if price.commodity == commodity:
                return price.price_usd_per_tonne
        raise TraderDecisionError(
            f"Trader {self.identity.agent_id!r}: missing global price for "
            f"{commodity.value} in {snapshot.month.isoformat()}"
        )

    def _regional_product_inventory(
        self, snapshot: MarketSnapshot, commodity: Commodity
    ) -> float:
        """Read regional product tonnes without inferring ownership or missing stock."""
        for stock in snapshot.inventories:
            if stock.region == self.identity.region and stock.commodity == commodity:
                return stock.quantity_tonnes
        raise TraderDecisionError(
            f"Trader {self.identity.agent_id!r}: missing {commodity.value} inventory "
            f"for {self.identity.region.value} in {snapshot.month.isoformat()}"
        )

    def _purchase_fraction(self, price_ratio: float) -> float:
        """Reduce the requested share of purchase capacity as soybean prices rise."""
        if price_ratio < self.config.low_price_ratio:
            return self.config.low_purchase_fraction

        if price_ratio < self.config.high_price_ratio:
            return self.config.normal_purchase_fraction

        return self.config.high_purchase_fraction

    def _sale_fraction(self, price_ratio: float) -> float:
        """Increase the requested share of product stock as its price rises."""
        if price_ratio < self.config.low_price_ratio:
            return self.config.low_sale_fraction

        if price_ratio < self.config.high_price_ratio:
            return self.config.normal_sale_fraction

        return self.config.high_sale_fraction
