"""Validate comparable histories and derive metrics from recorded market activity."""

import math
from collections.abc import Iterable
from datetime import date
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soybean_sim.agents import DecisionAction
from soybean_sim.models import Commodity, Region
from soybean_sim.simulation import MonthlyResult, SimulationResult

from ._drought import ScenarioError

_Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
_Key = tuple[Region, Commodity]


class MetricComparison(BaseModel):
    """Keep paired values; differences always mean drought minus baseline.

    Units come from the containing field: tonnes, USD/t, or dimensionless values.
    Percentage difference is undefined (None) for a zero baseline.

    Args:
        baseline (float): Finite baseline observation.
        drought (float): Finite drought observation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    baseline: _Finite
    drought: _Finite

    @property
    def difference(self) -> float:
        """Return the signed change.

        Returns:
            float: Drought minus baseline in the original units.
        """
        return self.drought - self.baseline

    @property
    def percentage_difference(self) -> float | None:
        """Express change relative to a nonzero baseline.

        Returns:
            float | None: Percentage change, or None when the baseline is zero.
        """
        return self.difference / self.baseline * 100 if self.baseline else None

    @model_validator(mode="after")
    def differences_are_finite(self) -> Self:
        """Reject arithmetic overflow in derived comparisons.

        Returns:
            Self: Validated finite metric pair.
        """
        percentage = self.percentage_difference
        if not math.isfinite(self.difference) or (
            percentage is not None and not math.isfinite(percentage)
        ):
            raise ValueError("comparison difference or percentage overflow")
        return self


class RegionalMarketComparison(BaseModel):
    """Compare one regional commodity using observed prices and executed activity.

    Args:
        region (Region): Compared market region.
        commodity (Commodity): Compared commodity.
        price_usd_per_tonne (MetricComparison): Same-month observed regional prices.
        inventory_tonnes (MetricComparison): Ending stocks.
        imports_tonnes (MetricComparison): Executed inbound trades.
        exports_tonnes (MetricComparison): Executed outbound trades.
        net_trade_tonnes (MetricComparison): Imports minus exports.
        supply_tonnes (MetricComparison): Recorded physical supply pressure.
        demand_tonnes (MetricComparison): Recorded physical demand pressure.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    region: Region
    commodity: Commodity
    price_usd_per_tonne: MetricComparison
    inventory_tonnes: MetricComparison
    imports_tonnes: MetricComparison
    exports_tonnes: MetricComparison
    net_trade_tonnes: MetricComparison
    supply_tonnes: MetricComparison
    demand_tonnes: MetricComparison


class MonthlyComparison(BaseModel):
    """Derive a single month's metrics without copying simulation histories.

    Args:
        month (date): Shared observed month.
        markets (tuple[RegionalMarketComparison, ...]): Sorted regional commodities.
        crushing_tonnes: Sorted (region, executed soybean input comparison) pairs.
        trade_volume_tonnes (MetricComparison): Executed trade volume, counted once.
        baseline_trade_count (int): Number of baseline executions.
        drought_trade_count (int): Number of drought executions.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    month: Annotated[date, Field(strict=True)]
    markets: tuple[RegionalMarketComparison, ...]
    crushing_tonnes: tuple[tuple[Region, MetricComparison], ...]
    trade_volume_tonnes: MetricComparison
    baseline_trade_count: Annotated[int, Field(strict=True, ge=0)]
    drought_trade_count: Annotated[int, Field(strict=True, ge=0)]


def validate_histories(baseline: SimulationResult, drought: SimulationResult) -> None:
    """Require identical periods/initial conditions and complete physical observations.

    Args:
        baseline (SimulationResult): Revalidated baseline history.
        drought (SimulationResult): Revalidated drought history.

    Returns:
        None: Invalid coverage or inputs raise a descriptive ValueError.
    """
    if tuple(item.month for item in baseline.monthly_results) != tuple(
        item.month for item in drought.monthly_results
    ):
        raise ValueError("comparison histories must cover identical months")
    first, second = baseline.initial_state.snapshot, drought.initial_state.snapshot
    if (
        first.month != second.month
        or {item.commodity: item for item in first.prices}
        != {item.commodity: item for item in second.prices}
        or {
            (item.region, item.commodity): item.quantity_tonnes
            for item in first.inventories
        }
        != {
            (item.region, item.commodity): item.quantity_tonnes
            for item in second.inventories
        }
    ):
        raise ValueError("comparison histories must have the same initial conditions")
    regions = {item.region for item in first.inventories}
    if not regions:
        raise ValueError("comparison requires represented inventory regions")
    expected = {(region, commodity) for region in regions for commodity in Commodity}
    for label, result in (("baseline", baseline), ("drought", drought)):
        for monthly in result.monthly_results:
            _validate_month(monthly, expected, label)
    initial_prices = [
        {
            (item.region, item.commodity): item
            for item in result.monthly_results[0].regional_prices
        }
        for result in (baseline, drought)
    ]
    if initial_prices[0] != initial_prices[1]:
        raise ValueError(
            "comparison histories must have the same initial regional prices"
        )


def _validate_month(monthly: MonthlyResult, expected: set[_Key], label: str) -> None:
    """Check full observed coverage and represented execution endpoints.

    Args:
        monthly (MonthlyResult): Revalidated completed month.
        expected (set[tuple[Region, Commodity]]): Complete regional commodity keys.
        label (str): Scenario name used in error context.

    Returns:
        None: Missing physical observations or incompatible executions are rejected.
    """
    context = f"{label} {monthly.month}"
    if not monthly.physical_cycle:
        raise ValueError(f"{context}: comparison requires physical-cycle records")
    for name, records in (
        ("regional prices", monthly.regional_prices),
        ("market pressures", monthly.market_pressures),
        ("opening inventories", monthly.starting_state.snapshot.inventories),
        ("ending inventories", monthly.ending_state.snapshot.inventories),
    ):
        if {(item.region, item.commodity) for item in records} != expected:
            raise ValueError(f"{context}: incompatible or missing {name} coverage")
    if any(
        (item.decision.agent.region, Commodity.SOYBEANS) not in expected
        for item in monthly.settlement_results
    ):
        raise ValueError(f"{context}: crushing outside represented regions")
    if any(
        (region, trade.commodity) not in expected
        for trade in monthly.executed_trades
        for region in (trade.source_region, trade.destination_region)
    ):
        raise ValueError(f"{context}: trade outside represented regions")


def monthly_metrics(
    baseline: MonthlyResult, drought: MonthlyResult
) -> MonthlyComparison:
    """Summarize validated activity using only recorded executions and observations.

    Args:
        baseline (MonthlyResult): Validated baseline month.
        drought (MonthlyResult): Validated same-month drought result.

    Returns:
        MonthlyComparison: Sorted metrics, including real zero execution totals.
    """
    regions = sorted(
        {stock.region for stock in baseline.ending_state.snapshot.inventories}
    )
    return MonthlyComparison(
        month=baseline.month,
        markets=_regional_metrics(baseline, drought),
        crushing_tonnes=tuple(
            (
                region,
                MetricComparison(
                    baseline=_executed_crushing(baseline, region),
                    drought=_executed_crushing(drought, region),
                ),
            )
            for region in regions
        ),
        trade_volume_tonnes=MetricComparison(
            baseline=_sum(item.quantity_tonnes for item in baseline.executed_trades),
            drought=_sum(item.quantity_tonnes for item in drought.executed_trades),
        ),
        baseline_trade_count=len(baseline.executed_trades),
        drought_trade_count=len(drought.executed_trades),
    )


def _regional_metrics(
    baseline: MonthlyResult, drought: MonthlyResult
) -> tuple[RegionalMarketComparison, ...]:
    """Join exact keys already checked for complete observation coverage.

    Args:
        baseline (MonthlyResult): Baseline monthly observations.
        drought (MonthlyResult): Matching drought observations.

    Returns:
        tuple[RegionalMarketComparison, ...]: Paired prices, stocks, trades, pressures.
    """
    base_prices = {
        (item.region, item.commodity): item.price_usd_per_tonne
        for item in baseline.regional_prices
    }
    dry_prices = {
        (item.region, item.commodity): item.price_usd_per_tonne
        for item in drought.regional_prices
    }
    base_stocks = {
        (item.region, item.commodity): item.quantity_tonnes
        for item in baseline.ending_state.snapshot.inventories
    }
    dry_stocks = {
        (item.region, item.commodity): item.quantity_tonnes
        for item in drought.ending_state.snapshot.inventories
    }
    base_pressures = {
        (item.region, item.commodity): item for item in baseline.market_pressures
    }
    dry_pressures = {
        (item.region, item.commodity): item for item in drought.market_pressures
    }
    metrics = []
    for region, commodity in sorted(base_prices):
        key = (region, commodity)
        base_imports, base_exports = _trade_totals(baseline, key)
        dry_imports, dry_exports = _trade_totals(drought, key)
        metrics.append(
            RegionalMarketComparison(
                region=region,
                commodity=commodity,
                price_usd_per_tonne=MetricComparison(
                    baseline=base_prices[key], drought=dry_prices[key]
                ),
                inventory_tonnes=MetricComparison(
                    baseline=base_stocks[key], drought=dry_stocks[key]
                ),
                imports_tonnes=MetricComparison(
                    baseline=base_imports, drought=dry_imports
                ),
                exports_tonnes=MetricComparison(
                    baseline=base_exports, drought=dry_exports
                ),
                net_trade_tonnes=MetricComparison(
                    baseline=base_imports - base_exports,
                    drought=dry_imports - dry_exports,
                ),
                supply_tonnes=MetricComparison(
                    baseline=base_pressures[key].supply_tonnes,
                    drought=dry_pressures[key].supply_tonnes,
                ),
                demand_tonnes=MetricComparison(
                    baseline=base_pressures[key].demand_tonnes,
                    drought=dry_pressures[key].demand_tonnes,
                ),
            )
        )
    return tuple(metrics)


def _executed_crushing(monthly: MonthlyResult, region: Region) -> float:
    """Sum actual soybean inputs; requests and unmatched intentions never contribute.

    Args:
        monthly (MonthlyResult): Recorded settlement activity.
        region (Region): Region whose crushing is measured.

    Returns:
        float: Executed input tonnes, including zero for no crushing.
    """
    return _sum(
        item.executed_tonnes
        for item in monthly.settlement_results
        if item.decision.action == DecisionAction.CRUSH
        and item.decision.agent.region == region
    )


def _trade_totals(monthly: MonthlyResult, key: _Key) -> tuple[float, float]:
    """Count matching imports and exports once from executed transfers.

    Args:
        monthly (MonthlyResult): Completed trade executions.
        key (tuple[Region, Commodity]): Regional commodity to measure.

    Returns:
        tuple[float, float]: Imports and exports in tonnes; absent executions mean zero.
    """
    region, commodity = key
    imports = [
        trade.quantity_tonnes
        for trade in monthly.executed_trades
        if trade.destination_region == region and trade.commodity == commodity
    ]
    exports = [
        trade.quantity_tonnes
        for trade in monthly.executed_trades
        if trade.source_region == region and trade.commodity == commodity
    ]
    for trade in monthly.external_trade_flows:
        if (trade.region, trade.commodity) == key:
            (imports if trade.is_import else exports).append(trade.quantity_tonnes)
    return _sum(imports), _sum(exports)


def _sum(quantities: Iterable[float]) -> float:
    """Aggregate executed quantities stably and reject overflow.

    Args:
        quantities (Iterable[float]): Validated nonnegative executions in tonnes.

    Returns:
        float: Finite total; an empty activity collection has a real zero total.
    """
    try:
        total = math.fsum(sorted(quantities))
    except OverflowError as exc:
        raise ScenarioError("Executed comparison quantity overflow") from exc
    if not math.isfinite(total):
        raise ScenarioError("Executed comparison quantity must be finite")
    return total
