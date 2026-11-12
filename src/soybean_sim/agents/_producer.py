"""Offer existing regional soybean stocks using configurable price-response bands."""

from dataclasses import dataclass
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soybean_sim.models import Commodity, MarketSnapshot

from ._models import AgentDecision, AgentIdentity, AgentType, DecisionAction

_PositiveFinite = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
_SupplyFraction = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


class ProducerConfig(BaseModel):
    """Set a USD/tonne price baseline, ordered bands, and stock offer fractions.

    Args:
        reference_price_usd_per_tonne (float): Positive soybean reference price in USD
            per metric tonne.
        low_price_ratio (float): Market/reference price ratio below which the low supply
            fraction applies.
        high_price_ratio (float): Market/reference price ratio at which the high supply
            fraction begins.
        low_supply_fraction (float): Share of observed regional stock offered below the
            low price threshold.
        normal_supply_fraction (float): Share of observed regional stock offered between
            the price thresholds.
        high_supply_fraction (float): Share of observed regional stock offered at or
            above the high threshold.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    reference_price_usd_per_tonne: _PositiveFinite
    low_price_ratio: _PositiveFinite = 0.90
    high_price_ratio: _PositiveFinite = 1.10
    low_supply_fraction: _SupplyFraction = 0.20
    normal_supply_fraction: _SupplyFraction = 0.50
    high_supply_fraction: _SupplyFraction = 0.80

    @model_validator(mode="after")
    def response_bands_are_ordered(self) -> Self:
        """Require increasing price thresholds and nondecreasing supply fractions.

        Returns:
            Self: The validated producer configuration instance.
        """
        if self.high_price_ratio <= self.low_price_ratio:
            raise ValueError("high_price_ratio must be greater than low_price_ratio")

        if not (
            self.low_supply_fraction
            <= self.normal_supply_fraction
            <= self.high_supply_fraction
        ):
            raise ValueError("supply fractions must be nondecreasing from low to high")

        return self


class ProducerDecisionError(ValueError):
    """A producer cannot decide because its required market observations are missing."""


@dataclass(frozen=True)
class Producer:
    """Represent aggregate regional producers offering existing soybean inventory.

    Args:
        identity (AgentIdentity): Identity with the producer role and home region.
        config (ProducerConfig): Reference price, price bands, and stock offer
            fractions.
    """

    identity: AgentIdentity
    config: ProducerConfig

    def __post_init__(self) -> None:
        """Require the supplied identity to identify a producer agent.

        Returns:
            None: No value is returned.
        """
        if self.identity.agent_type != AgentType.PRODUCER:
            raise ValueError("Producer identity must have agent_type='producer'")

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Offer a price-dependent share of regional stock without executing a sale.

        Args:
            snapshot (MarketSnapshot): Current-month global soybean price and regional
                soybean stock.

        Returns:
            tuple[AgentDecision, ...]: One regional soybean sale offer, or an empty
                tuple when observed stock is zero.
        """
        market_price = self._soybean_price(snapshot)
        available_soybeans = self._available_soybeans(snapshot)
        if available_soybeans == 0:
            return ()
        offered_tonnes = available_soybeans * self._supply_fraction(market_price)

        return (
            AgentDecision(
                agent=self.identity,
                month=snapshot.month,
                action=DecisionAction.OFFER_FOR_SALE,
                commodity=Commodity.SOYBEANS,
                quantity_tonnes=offered_tonnes,
            ),
        )

    def _soybean_price(self, snapshot: MarketSnapshot) -> float:
        """Read the global soybean price, reporting a missing observation explicitly.

        Args:
            snapshot (MarketSnapshot): Current-month global reference prices.

        Returns:
            float: Global soybean reference price in USD per metric tonne.
        """
        for price in snapshot.prices:
            if price.commodity == Commodity.SOYBEANS:
                return price.price_usd_per_tonne
        raise ProducerDecisionError(
            f"Producer {self.identity.agent_id!r}: missing global soybean price "
            f"for {snapshot.month.isoformat()}"
        )

    def _available_soybeans(self, snapshot: MarketSnapshot) -> float:
        """Read this region's soybean tonnes, distinguishing known zero from missing.

        Args:
            snapshot (MarketSnapshot): Current-month regional inventory observations.

        Returns:
            float: Observed soybean stock in this producer's region, including known
                zero.
        """
        for stock in snapshot.inventories:
            if (
                stock.region == self.identity.region
                and stock.commodity == Commodity.SOYBEANS
            ):
                return stock.quantity_tonnes
        raise ProducerDecisionError(
            f"Producer {self.identity.agent_id!r}: missing soybean inventory "
            f"for {self.identity.region.value} in {snapshot.month.isoformat()}"
        )

    def _supply_fraction(self, market_price: float) -> float:
        """Choose a supply fraction from the market/reference price ratio.

        Args:
            market_price (float): Global soybean reference price in USD per metric
                tonne.

        Returns:
            float: Configured share of regional stock for the applicable price-ratio
                band.
        """
        price_ratio = market_price / self.config.reference_price_usd_per_tonne
        if price_ratio < self.config.low_price_ratio:
            return self.config.low_supply_fraction
        if price_ratio < self.config.high_price_ratio:
            return self.config.normal_supply_fraction
        return self.config.high_supply_fraction
