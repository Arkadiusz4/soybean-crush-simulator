"""Compose local historical records into explicitly assumed monthly market inputs."""

import hashlib
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soybean_sim.data import build_simulation_input
from soybean_sim.data.usda import USDADataError, load_usda_psd_csv
from soybean_sim.data.world_bank import WorldBankDataError, load_world_bank_prices
from soybean_sim.market import (
    ExternalMarket,
    ExternalTradeFlow,
    MarketFlow,
    MarketState,
    MonthlyFlowConfig,
    generate_monthly_flows,
    validate_external_trades,
)
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    MarketBalance,
    MarketSnapshot,
    Region,
)
from soybean_sim.simulation import next_month

_MONTHS_PER_YEAR: Final = 12


class HistoricalMetadata(BaseModel):
    """Describe source identity and the distinction between reporting periods.

    Args:
        usda_source (str): Local CSV path or explicit in-memory provenance label.
        world_bank_source (str): Local XLSX path or in-memory provenance label.
        marketing_year (str): Selected USDA source label, independent of calendar year.
        reference_price_month (date): Actual World Bank observation month.
        months (tuple[date, ...]): Requested consecutive simulation calendar months.
        allocation (MonthlyFlowConfig): Assumed monthly shares of annual quantities.
        usda_sha256 (str | None): File digest when loaded from disk.
        world_bank_sha256 (str | None): File digest when loaded from disk.
        include_historical_trade (bool): Uniform gross boundary trade replaces matching.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    usda_source: str
    world_bank_source: str
    marketing_year: str
    reference_price_month: date = Field(strict=True)
    months: tuple[date, ...] = Field(min_length=1)
    allocation: MonthlyFlowConfig
    usda_sha256: str | None = None
    world_bank_sha256: str | None = None
    include_historical_trade: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def dates_are_aligned(self) -> Self:
        """Require first-of-month observations and consecutive simulation dates.

        Returns:
            Self: Metadata with aligned observation and initial simulation months.
        """
        if self.reference_price_month != self.months[0]:
            raise ValueError(
                "reference price month must equal the simulation start month"
            )
        for index, month in enumerate(self.months):
            if type(month) is not date or month.day != 1:
                raise ValueError("simulation months must be first-of-month dates")
            if index and month != next_month(self.months[index - 1]):
                raise ValueError("simulation months must be consecutive")
        return self

    @property
    def assumptions(self) -> tuple[str, ...]:
        """Expose the same time and accounting caveats to API and terminal consumers.

        Returns:
            tuple[str, ...]: Explicit assumptions, not historical reconstruction claims.
        """
        return (
            f"USDA {self.marketing_year} beginning stocks are assumed available at "
            f"{self.months[0]}; they are marketing-year-start stocks, "
            "not reconstructed calendar inventories.",
            "Monthly flows are modeled calendar allocations of annual USDA quantities, "
            "not observed monthly production or consumption.",
            f"Global prices use {self.reference_price_month} observations throughout "
            "the run; regional prices are simulated, not historical price replay.",
            "Meal/oil SUPPLY is reference-only. Physical products come from executed "
            "crushing; soybean consumption is handled by crushing only.",
            (
                "USDA annual imports/exports are modeled uniformly (/12) through an "
                "external clearing boundary, not observed monthly or bilateral trade. "
                "Imports precede crushing; exports follow consumption. Agent matching "
                "is disabled to avoid double-counting these gross trade totals."
                if self.include_historical_trade
                else "USDA imports/exports are references only. Built-in Traders buy "
                "soybeans only, so meal/oil cannot be imported by these agents."
            ),
            "Prices react to monthly physical flows, not direct inventory scarcity; "
            "successful execution is not economic calibration.",
        )


class HistoricalMarketInputs(BaseModel):
    """Retain original selected observations alongside modeled monthly inputs.

    Args:
        state (MarketState): Initial global references and unadjusted beginning stocks.
        balances (tuple[MarketBalance, ...]): Selected original annual observations.
        reference_prices (tuple[CommodityPrice, ...]): Original monthly observations.
        flows (tuple[MarketFlow, ...]): Modeled allocations, without historical scaling.
        metadata (HistoricalMetadata): Source identity and explicit period assumptions.
        external_trade_flows (tuple[ExternalTradeFlow, ...]): Boundary schedule.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    state: MarketState
    balances: tuple[MarketBalance, ...]
    reference_prices: tuple[CommodityPrice, ...]
    flows: tuple[MarketFlow, ...]
    metadata: HistoricalMetadata
    external_trade_flows: tuple[ExternalTradeFlow, ...] = ()


class HistoricalPreflight(BaseModel):
    """Separate blocking conditions from caveats; clearance is not completed execution.

    Args:
        metadata (HistoricalMetadata | None): Provenance when available.
        warnings (tuple[str, ...]): Modeling caveats requiring interpretation.
        blocking_errors (tuple[str, ...]): Conditions that prevent physical execution.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    metadata: HistoricalMetadata | None = None
    warnings: tuple[str, ...] = ()
    blocking_errors: tuple[str, ...] = ()

    @property
    def can_run(self) -> bool:
        """Indicate only that no blocking preflight condition was detected.

        Returns:
            bool: Whether execution may be attempted, without a success guarantee.
        """
        return not self.blocking_errors


class HistoricalMarketError(ValueError):
    """Carry preparation, preflight, or execution errors without partial results."""

    def __init__(self, diagnostics: HistoricalPreflight) -> None:
        """Retain inspectable errors and warnings for API and terminal callers.

        Args:
            diagnostics (HistoricalPreflight): Source context and blocking reasons.

        Returns:
            None: Initializes the exception without a partial simulation history.
        """
        self.diagnostics = diagnostics
        super().__init__("; ".join(diagnostics.blocking_errors))


def prepare_historical_market(
    balances: Iterable[MarketBalance],
    prices: Iterable[CommodityPrice],
    *,
    marketing_year: str,
    price_month: date,
    start_month: date,
    months: int,
    flow_config: MonthlyFlowConfig | None = None,
    usda_source: str = "in-memory USDA-format records",
    world_bank_source: str = "in-memory World Bank-format records",
    include_historical_trade: bool = False,
) -> HistoricalMarketInputs:
    """Select historical inputs and allocate annual flows without altering quantities.

    Args:
        balances (Iterable[MarketBalance]): Existing loader observations.
        prices (Iterable[CommodityPrice]): Existing global reference observations.
        marketing_year (str): Explicit USDA year label.
        price_month (date): Explicit reference-price month; must equal start_month.
        start_month (date): First simulation calendar month.
        months (int): Positive duration within the supported date range.
        flow_config (MonthlyFlowConfig | None): Allocation assumptions, default uniform.
        usda_source (str): Source label retained in metadata.
        world_bank_source (str): Source label retained in metadata.
        include_historical_trade (bool): Allocate gross imports/exports uniformly /12.

    Returns:
        HistoricalMarketInputs: Complete four-region inputs or diagnostic exception.
    """
    metadata = None
    try:
        allocation = MonthlyFlowConfig() if flow_config is None else flow_config
        if not isinstance(allocation, MonthlyFlowConfig):
            raise ValueError("flow_config must be MonthlyFlowConfig")
        metadata = HistoricalMetadata(
            usda_source=usda_source,
            world_bank_source=world_bank_source,
            marketing_year=marketing_year,
            reference_price_month=price_month,
            months=_calendar_months(start_month, months),
            allocation=MonthlyFlowConfig.model_validate(
                allocation.model_dump(warnings=False)
            ),
            include_historical_trade=include_historical_trade,
        )
        annual = _validated_observations(balances, MarketBalance)
        references = _validated_observations(prices, CommodityPrice)
        initial = build_simulation_input(
            annual,
            references,
            marketing_year=marketing_year,
            price_month=price_month,
            regions=tuple(Region),
            commodities=tuple(Commodity),
        )
        missing = _missing_annual_values(initial.balances)
        if missing:
            raise HistoricalMarketError(
                HistoricalPreflight(
                    metadata=metadata,
                    warnings=metadata.assumptions,
                    blocking_errors=missing,
                )
            )
        state = MarketState.from_snapshot(
            MarketSnapshot(
                month=start_month,
                prices=initial.prices,
                inventories=initial.inventories,
            )
        )
        flows = tuple(
            flow
            for month in metadata.months
            for flow in generate_monthly_flows(
                initial.balances, month=month, config=metadata.allocation
            )
        )
        return HistoricalMarketInputs(
            state=state,
            balances=initial.balances,
            reference_prices=initial.prices,
            flows=flows,
            metadata=metadata,
            external_trade_flows=(
                _allocate_external_trades(initial.balances, metadata.months)
                if include_historical_trade
                else ()
            ),
        )
    except HistoricalMarketError:
        raise
    except ValueError as exc:
        raise HistoricalMarketError(
            HistoricalPreflight(
                metadata=metadata,
                warnings=() if metadata is None else metadata.assumptions,
                blocking_errors=(f"Historical preparation blocked: {exc}",),
            )
        ) from exc


def load_historical_market(
    usda_path: str | Path,
    world_bank_path: str | Path,
    *,
    marketing_year: str = "2024",
    price_month: date = date(2025, 1, 1),
    start_month: date = date(2025, 1, 1),
    months: int = 12,
    flow_config: MonthlyFlowConfig | None = None,
    include_historical_trade: bool = False,
) -> HistoricalMarketInputs:
    """Use existing local loaders and retain source hashes without downloading files.

    Args:
        usda_path (str | Path): Existing local PSD oilseeds CSV.
        world_bank_path (str | Path): Existing local Pink Sheet monthly workbook.
        marketing_year (str): Independent USDA selection label.
        price_month (date): World Bank month to select.
        start_month (date): Simulation start, equal to the selected reference month.
        months (int): Duration, default January through December 2025.
        flow_config (MonthlyFlowConfig | None): Modeled allocation weights.
        include_historical_trade (bool): Include uniformly allocated boundary transfers.

    Returns:
        HistoricalMarketInputs: Selected data with reproducible file provenance.
    """
    csv_path, xlsx_path = Path(usda_path).resolve(), Path(world_bank_path).resolve()
    try:
        balances = load_usda_psd_csv(
            csv_path,
            marketing_year=marketing_year,
            regions=tuple(Region),
            commodities=tuple(Commodity),
        )
        prices = load_world_bank_prices(
            xlsx_path, start_month=price_month, end_month=price_month
        )
        prepared = prepare_historical_market(
            balances,
            prices,
            marketing_year=marketing_year,
            price_month=price_month,
            start_month=start_month,
            months=months,
            flow_config=flow_config,
            usda_source=str(csv_path),
            world_bank_source=str(xlsx_path),
            include_historical_trade=include_historical_trade,
        )
        metadata = prepared.metadata.model_copy(
            update={
                "usda_sha256": _file_digest(csv_path),
                "world_bank_sha256": _file_digest(xlsx_path),
            }
        )
        return prepared.model_copy(update={"metadata": metadata})
    except HistoricalMarketError:
        raise
    except (USDADataError, WorldBankDataError, OSError, ValueError) as exc:
        raise HistoricalMarketError(
            HistoricalPreflight(
                blocking_errors=(f"Historical sources {csv_path} / {xlsx_path}: {exc}",)
            )
        ) from exc


def _calendar_months(start: date, count: int) -> tuple[date, ...]:
    """Build a bounded consecutive period without advancing past the final month.

    Args:
        start (date): Explicit first day of the initial month.
        count (int): Positive number of simulation steps.

    Returns:
        tuple[date, ...]: Complete requested dates, including calendar-year transitions.
    """
    if (
        type(count) is not int
        or count <= 0
        or type(start) is not date
        or start.day != 1
    ):
        raise ValueError(
            "duration must be positive and start must be a first-of-month date"
        )
    dates = [start]
    for _ in range(count - 1):
        dates.append(next_month(dates[-1]))
    return tuple(dates)


def _validated_observations[Record: (MarketBalance, CommodityPrice)](
    records: Iterable[Record],
    model: type[Record],
) -> tuple[Record, ...]:
    """Revalidate original records before selecting periods or reading quantities.

    Args:
        records (Iterable[Record]): Candidate source observations.
        model (type[Record]): Required annual balance or monthly price model.

    Returns:
        tuple[Record, ...]: Validated records without missing-value defaults.
    """
    try:
        observations = iter(records)
    except TypeError as exc:
        raise ValueError(f"Expected an iterable of {model.__name__}") from exc
    validated = []
    for position, item in enumerate(observations, start=1):
        if not isinstance(item, model):
            raise ValueError(f"Observation {position}: expected {model.__name__}")
        validated.append(model.model_validate(item.model_dump(warnings=False)))
    return tuple(validated)


def _missing_annual_values(balances: tuple[MarketBalance, ...]) -> tuple[str, ...]:
    """Require complete production and product consumption for historical allocation.

    Args:
        balances (tuple[MarketBalance, ...]): Selected annual records.

    Returns:
        tuple[str, ...]: Missing production or product-consumption fields.
    """
    errors = []
    for balance in balances:
        fields = (
            ("production_tonnes",)
            if balance.commodity == Commodity.SOYBEANS
            else ("production_tonnes", "domestic_consumption_tonnes")
        )
        for field in fields:
            if getattr(balance, field) is None:
                errors.append(
                    f"{balance.region.value}/{balance.commodity.value} marketing year "
                    f"{balance.marketing_year}: missing {field}; no value inferred"
                )
    return tuple(errors)


def _file_digest(path: Path) -> str:
    """Identify the exact local dataset used for a reproducible selection.

    Args:
        path (Path): Successfully loaded local source file.

    Returns:
        str: SHA-256 of the file's original bytes.
    """
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _allocate_external_trades(
    balances: tuple[MarketBalance, ...],
    months: tuple[date, ...],
) -> tuple[ExternalTradeFlow, ...]:
    """Allocate observed gross trade uniformly without guessing bilateral origins.

    Args:
        balances (tuple[MarketBalance, ...]): Validated selected annual observations.
        months (tuple[date, ...]): Explicit simulation months, each receiving 1/12.

    Returns:
        tuple[ExternalTradeFlow, ...]: Identified boundary transfers, including zeros.
    """
    flows = []
    for balance in balances:
        for field, source, destination in (
            ("imports_tonnes", ExternalMarket.CLEARING_BOUNDARY, balance.region),
            ("exports_tonnes", balance.region, ExternalMarket.CLEARING_BOUNDARY),
        ):
            annual_quantity = getattr(balance, field)
            if annual_quantity is None:
                raise ValueError(
                    f"{balance.region.value}/{balance.commodity.value} marketing year "
                    f"{balance.marketing_year}: missing {field} for physical trade"
                )
            for month in months:
                flows.append(
                    ExternalTradeFlow(
                        month=month,
                        source=source,
                        destination=destination,
                        commodity=balance.commodity,
                        quantity_tonnes=annual_quantity / _MONTHS_PER_YEAR,
                    )
                )
    return validate_external_trades(flows)
