"""Scale historical processing limits from observed annual soybean crush inputs."""

import math
from typing import Final

from soybean_sim.agents import CrusherConfig
from soybean_sim.data import select_market_data
from soybean_sim.models import Commodity, MarketBalance, Region

from ._historical import (
    HistoricalMarketError,
    HistoricalMarketInputs,
    HistoricalPreflight,
)

_MONTHS_PER_YEAR: Final = 12


def calibrate_historical_crushers(
    prepared: HistoricalMarketInputs,
    *,
    processing_cost_usd_per_tonne: float,
    calibrate_yields: bool = False,
) -> dict[Region, CrusherConfig]:
    """Use annual USDA crush divided by twelve as a modeled monthly input limit.

    Throughput is not observed nameplate capacity. Optional product/input ratios
    retain an explicit residual; they are never normalized. Margin rules stay active.

    Args:
        prepared (HistoricalMarketInputs): Selected balances with annual crush data.
        processing_cost_usd_per_tonne (float): Explicit assumed processing cost.
        calibrate_yields (bool): Derive meal/oil/residual fractions from annual data;
            False retains the existing 80/20, zero-residual defaults.

    Returns:
        dict[Region, CrusherConfig]: Regional rules, sorted by region, or diagnostics.
    """
    metadata = None
    try:
        if not isinstance(prepared, HistoricalMarketInputs):
            raise ValueError("prepared must be HistoricalMarketInputs")
        if type(calibrate_yields) is not bool:
            raise ValueError("calibrate_yields must be bool")
        data = HistoricalMarketInputs.model_validate(
            prepared.model_dump(warnings=False)
        )
        metadata = data.metadata
        balances, _ = select_market_data(
            data.balances,
            data.reference_prices,
            marketing_year=metadata.marketing_year,
            price_month=metadata.reference_price_month,
            regions=tuple(Region),
            commodities=tuple(Commodity) if calibrate_yields else (Commodity.SOYBEANS,),
        )
        indexed = {(item.region, item.commodity): item for item in balances}
        configs = {}
        for region in sorted(Region):
            balance = indexed[region, Commodity.SOYBEANS]
            annual_crush = balance.crush_tonnes
            if annual_crush is None or annual_crush <= 0:
                raise ValueError(
                    f"{balance.region.value}/soybeans marketing year "
                    f"{balance.marketing_year}: positive observed crush_tonnes "
                    "required to derive capacity; no fallback inferred"
                )
            yields = {}
            if calibrate_yields:
                meal, oil, residual = _historical_yields(
                    annual_crush,
                    indexed[region, Commodity.SOYBEAN_MEAL],
                    indexed[region, Commodity.SOYBEAN_OIL],
                )
                yields = dict(
                    meal_yield_fraction=meal,
                    oil_yield_fraction=oil,
                    residual_yield_fraction=residual,
                )
            configs[balance.region] = CrusherConfig(
                monthly_capacity_tonnes=annual_crush / _MONTHS_PER_YEAR,
                processing_cost_usd_per_tonne=processing_cost_usd_per_tonne,
                **yields,
            )
        return configs
    except ValueError as exc:
        raise HistoricalMarketError(
            HistoricalPreflight(
                metadata=metadata,
                warnings=() if metadata is None else metadata.assumptions,
                blocking_errors=(f"Historical capacity calibration blocked: {exc}",),
            )
        ) from exc


def _historical_yields(
    annual_crush: float,
    meal: MarketBalance,
    oil: MarketBalance,
) -> tuple[float, float, float]:
    """Derive unnormalized product ratios with an explicit unmodeled mass remainder.

    Args:
        annual_crush (float): Validated positive annual soybean input tonnes.
        meal (MarketBalance): Same-region/year soybean meal production observation.
        oil (MarketBalance): Same-region/year soybean oil production observation.

    Returns:
        tuple[float, float, float]: Meal, oil, and residual tonnes per input tonne.
    """
    meal_output, oil_output = meal.production_tonnes, oil.production_tonnes
    context = f"{meal.region.value} marketing year {meal.marketing_year}"
    if meal_output is None or oil_output is None:
        raise ValueError(
            f"{context}: meal/oil production required for yield calibration"
        )
    total_products = meal_output + oil_output
    if not math.isfinite(total_products) or not 0 < total_products <= annual_crush:
        raise ValueError(
            f"{context}: product output {total_products:g} t must be positive and "
            f"cannot exceed soybean crush {annual_crush:g} t; no normalization applied"
        )
    return (
        meal_output / annual_crush,
        oil_output / annual_crush,
        (annual_crush - total_products) / annual_crush,
    )
