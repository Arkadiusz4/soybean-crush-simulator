"""Calculate regional pressure from physical flows, crushing, and executed trades."""

import math
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Final

from pydantic import TypeAdapter, ValidationError

from soybean_sim.models import Commodity, NonNegativeTonnes, Region

from ._contracts import MarketFlow, MarketFlowType, MarketPressure, TradeExecution

_CRUSH_REL_TOLERANCE: Final = 1e-12
_CRUSH_ABS_TOLERANCE_TONNES: Final = 1e-9
_QUANTITY_ADAPTER: Final = TypeAdapter(NonNegativeTonnes)
_MarketKey = tuple[Region, Commodity]
_Contributions = dict[_MarketKey, list[float]]


class MarketPressureError(ValueError):
    """Invalid market activity or pressure calculation inputs."""


def calculate_market_pressures(
    *,
    month: date,
    regions: Iterable[Region],
    flows: Iterable[MarketFlow],
    trades: Iterable[TradeExecution],
    crushed_soybeans_tonnes: Mapping[Region, float],
    crushing_outputs_tonnes: Mapping[tuple[Region, Commodity], float],
) -> tuple[MarketPressure, ...]:
    """Calculate physical supply and demand without changing market inputs.

    Args:
        month (date): First day of the activity month.
        regions (Iterable[Region]): Nonempty, unique regions to cover.
        flows (Iterable[MarketFlow]): Monthly flows, including reference production.
        trades (Iterable[TradeExecution]): Executed physical trades.
        crushed_soybeans_tonnes (Mapping[Region, float]): Executed soybean input.
        crushing_outputs_tonnes (Mapping[tuple[Region, Commodity], float]): Actual
            meal/oil outputs; no yields or missing outputs are inferred.

    Returns:
        tuple[MarketPressure, ...]: All regional commodities in sorted order,
            with zero signals where no activity is reported.
    """
    if type(month) is not date or month.day != 1:
        raise MarketPressureError("month must be a date on the first day of a month")

    selected = _requested_regions(regions)
    supply: _Contributions = {
        (region, commodity): [] for region in selected for commodity in Commodity
    }
    demand: _Contributions = {key: [] for key in supply}

    _add_flows(flows, month, selected, supply, demand)
    _add_crushing(
        crushed_soybeans_tonnes, crushing_outputs_tonnes, selected, supply, demand
    )
    _add_trades(trades, month, selected, supply, demand)

    return tuple(
        MarketPressure(
            month=month,
            region=region,
            commodity=commodity,
            supply_tonnes=_sum_quantities(
                supply[region, commodity], f"{region.value}/{commodity.value} supply"
            ),
            demand_tonnes=_sum_quantities(
                demand[region, commodity], f"{region.value}/{commodity.value} demand"
            ),
        )
        for region, commodity in sorted(supply)
    )


def _requested_regions(regions: Iterable[Region]) -> set[Region]:
    """Validate a nonempty selection without collapsing duplicate regions.

    Args:
        regions (Iterable[Region]): Requested identifiers.

    Returns:
        set[Region]: Unique supported regions.
    """
    if isinstance(regions, (str, bytes)):
        raise MarketPressureError("regions must be a collection of region identifiers")

    try:
        selected = tuple(Region(region) for region in regions)
    except (TypeError, ValueError) as exc:
        raise MarketPressureError(f"Invalid requested regions: {exc}") from exc

    if not selected:
        raise MarketPressureError("regions must contain at least one region")

    if len(set(selected)) != len(selected):
        raise MarketPressureError("Duplicate requested regions")

    return set(selected)


def _activity_region(region: Region, regions: set[Region]) -> Region:
    """Resolve an activity region and require membership in the requested markets.

    Args:
        region (Region): Candidate region identifier.
        regions (set[Region]): Requested regions.

    Returns:
        Region: Validated activity region.
    """
    try:
        selected = Region(region)
    except (TypeError, ValueError) as exc:
        raise MarketPressureError(f"Invalid activity region: {region!r}") from exc

    if selected not in regions:
        raise MarketPressureError(
            f"Region {selected.value} is outside requested regions"
        )

    return selected


def _validated_records[Record: (MarketFlow, TradeExecution)](
    records: Iterable[Record], record_type: type[Record], month: date
) -> Iterable[Record]:
    """Revalidate typed monthly observations before accounting for them.

    Args:
        records (Iterable[Record]): Candidate activity records.
        record_type (type[Record]): Required flow or trade model.
        month (date): Expected activity month.

    Yields:
        Record: Revalidated record belonging to the requested month.
    """
    try:
        observations = iter(records)
    except TypeError as exc:
        raise MarketPressureError(
            f"Expected an iterable of {record_type.__name__}"
        ) from exc

    for record in observations:
        if not isinstance(record, record_type):
            raise MarketPressureError(f"Expected {record_type.__name__} records")

        try:
            validated = record_type.model_validate(record.model_dump(warnings=False))
        except ValidationError as exc:
            raise MarketPressureError(f"Invalid {record_type.__name__}: {exc}") from exc
        if validated.month != month:
            raise MarketPressureError(
                f"{record_type.__name__} month {validated.month} does not match {month}"
            )
        yield validated


def _add_flows(
    flows: Iterable[MarketFlow],
    month: date,
    regions: set[Region],
    supply: _Contributions,
    demand: _Contributions,
) -> None:
    """Count soybean supply and product consumption, excluding reference supply.

    Args:
        flows (Iterable[MarketFlow]): Candidate monthly flows.
        month (date): Activity month.
        regions (set[Region]): Requested regions.
        supply (_Contributions): Local supply counters.
        demand (_Contributions): Local demand counters.

    Returns:
        None: Contributions are appended to the local counters.
    """
    seen: set[tuple[Region, Commodity, MarketFlowType]] = set()
    for flow in _validated_records(flows, MarketFlow, month):
        region = _activity_region(flow.region, regions)

        key = (region, flow.commodity, flow.flow_type)
        if key in seen:
            raise MarketPressureError(
                f"Duplicate flow for {region.value}/"
                f"{flow.commodity.value}/{flow.flow_type.value}"
            )

        seen.add(key)

        if flow.flow_type == MarketFlowType.SUPPLY:
            if flow.commodity == Commodity.SOYBEANS:
                supply[region, flow.commodity].append(flow.quantity_tonnes)
            continue

        if flow.commodity == Commodity.SOYBEANS:
            raise MarketPressureError("Unsupported soybean consumption flow")
        demand[region, flow.commodity].append(flow.quantity_tonnes)


def _quantity(value: float, context: str) -> float:
    """Validate a finite nonnegative quantity using the existing tonne contract.

    Args:
        value (float): Candidate quantity in tonnes.
        context (str): Activity label for validation errors.

    Returns:
        float: Validated tonnes.
    """
    try:
        return _QUANTITY_ADAPTER.validate_python(value)
    except ValidationError as exc:
        raise MarketPressureError(f"Invalid quantity for {context}: {exc}") from exc


def _crushing_inputs(
    quantities: Mapping[Region, float], regions: set[Region]
) -> dict[Region, float]:
    """Validate actual soybean input quantities by region.

    Args:
        quantities (Mapping[Region, float]): Executed crushing inputs.
        regions (set[Region]): Requested regions.

    Returns:
        dict[Region, float]: Validated input tonnes.
    """
    if not isinstance(quantities, Mapping):
        raise MarketPressureError("crushed_soybeans_tonnes must be a mapping")

    return {
        _activity_region(region, regions): _quantity(
            value, f"{region!s} crushing input"
        )
        for region, value in quantities.items()
    }


def _crushing_outputs(
    quantities: Mapping[_MarketKey, float], regions: set[Region]
) -> dict[_MarketKey, float]:
    """Validate actual product outputs without imposing yield coefficients.

    Args:
        quantities (Mapping[tuple[Region, Commodity], float]): Product output tonnes.
        regions (set[Region]): Requested regions.

    Returns:
        dict[tuple[Region, Commodity], float]: Validated meal/oil outputs.
    """
    if not isinstance(quantities, Mapping):
        raise MarketPressureError("crushing_outputs_tonnes must be a mapping")
    outputs: dict[_MarketKey, float] = {}

    for key, value in quantities.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise MarketPressureError(
                "Crushing output keys must be (region, commodity)"
            )
        region = _activity_region(key[0], regions)

        try:
            commodity = Commodity(key[1])
        except (TypeError, ValueError) as exc:
            raise MarketPressureError(
                f"Invalid crushing output commodity: {key[1]!r}"
            ) from exc

        if commodity == Commodity.SOYBEANS:
            raise MarketPressureError(
                "Crushing outputs must contain meal or oil, not soybeans"
            )
        outputs[region, commodity] = _quantity(
            value, f"{region.value}/{commodity.value} crushing output"
        )

    return outputs


def _add_crushing(
    inputs: Mapping[Region, float],
    outputs: Mapping[_MarketKey, float],
    regions: set[Region],
    supply: _Contributions,
    demand: _Contributions,
) -> None:
    """Add executed crushing signals after checking outputs against input mass.

    Args:
        inputs (Mapping[Region, float]): Executed soybean input tonnes.
        outputs (Mapping[tuple[Region, Commodity], float]): Actual output tonnes.
        regions (set[Region]): Requested regions.
        supply (_Contributions): Local supply counters.
        demand (_Contributions): Local demand counters.

    Returns:
        None: Contributions are appended to the local counters.
    """
    validated_inputs = _crushing_inputs(inputs, regions)
    validated_outputs = _crushing_outputs(outputs, regions)

    for region in sorted(regions):
        input_tonnes = validated_inputs.get(region, 0)
        output_tonnes = _sum_quantities(
            [
                quantity
                for (output_region, _), quantity in validated_outputs.items()
                if output_region == region
            ],
            f"{region.value} crushing outputs",
        )

        if output_tonnes > input_tonnes and not math.isclose(
            output_tonnes,
            input_tonnes,
            rel_tol=_CRUSH_REL_TOLERANCE,
            abs_tol=_CRUSH_ABS_TOLERANCE_TONNES,
        ):
            raise MarketPressureError(
                f"Crushing outputs exceed soybean input for {region.value}: "
                f"{output_tonnes:g} > {input_tonnes:g} tonnes"
            )

    for region, quantity in validated_inputs.items():
        demand[region, Commodity.SOYBEANS].append(quantity)

    for key, quantity in validated_outputs.items():
        supply[key].append(quantity)


def _add_trades(
    trades: Iterable[TradeExecution],
    month: date,
    regions: set[Region],
    supply: _Contributions,
    demand: _Contributions,
) -> None:
    """Count each execution as source demand and destination supply.

    Args:
        trades (Iterable[TradeExecution]): Executed interregional trades.
        month (date): Activity month.
        regions (set[Region]): Requested regions.
        supply (_Contributions): Local supply counters.
        demand (_Contributions): Local demand counters.

    Returns:
        None: Contributions are appended to the local counters.
    """
    seen: set[TradeExecution] = set()
    for trade in _validated_records(trades, TradeExecution, month):
        source = _activity_region(trade.source_region, regions)
        destination = _activity_region(trade.destination_region, regions)

        if trade in seen:
            raise MarketPressureError(
                f"Duplicate trade from {trade.seller_agent_id!r} "
                f"to {trade.buyer_agent_id!r}"
            )
        seen.add(trade)
        demand[source, trade.commodity].append(trade.quantity_tonnes)
        supply[destination, trade.commodity].append(trade.quantity_tonnes)


def _sum_quantities(quantities: Iterable[float], context: str) -> float:
    """Sum contributions in a fixed order and reject overflowing totals.

    Args:
        quantities (Iterable[float]): Validated nonnegative tonne contributions.
        context (str): Activity label for overflow errors.

    Returns:
        float: Finite total tonnes, including zero for no contributions.
    """
    try:
        return math.fsum(sorted(quantities))
    except OverflowError as exc:
        raise MarketPressureError(f"Quantity overflow for {context}") from exc
