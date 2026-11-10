"""Read local Pink Sheet XLSX observations into validated global reference prices."""

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from soybean_sim.models import Commodity, CommodityPrice

from . import _mappings as mappings
from . import _parsing as parsing
from ._parsing import WorldBankDataError

_PriceKey = tuple[date, Commodity]
_Observations = dict[_PriceKey, parsing.ParsedPrice]
_SelectedRow = tuple[date, tuple[object, ...], str]


def load_world_bank_prices(
    path: str | Path,
    *,
    start_month: date | None = None,
    end_month: date | None = None,
    commodities: Iterable[Commodity] | None = None,
) -> tuple[CommodityPrice, ...]:
    """Load local XLSX prices sorted by month and commodity, without changing the file.

    Bounds are inclusive first-of-month dates. None selects all commodities; an
    empty selection or no matching observations returns an empty tuple. Blanks
    and ellipsis markers are skipped. Equivalent duplicates count once, while
    conflicting observations are invalid. No conversion or interpolation occurs.

    Args:
        path (str | Path): Path to a local World Bank monthly Pink Sheet XLSX workbook.
        start_month (date | None): Inclusive first-of-month lower bound, or None for no
            lower restriction.
        end_month (date | None): Inclusive first-of-month upper bound, or None for no
            upper restriction.
        commodities (Iterable[Commodity] | None): Commodities to retain; None selects
            all and an empty iterable selects none.

    Returns:
        tuple[CommodityPrice, ...]: Global USD-per-tonne prices sorted by month and
            commodity; an empty tuple when no observations match.
    """
    parsing.validate_month_range(start_month, end_month)
    selected_commodities = (
        set(Commodity)
        if commodities is None
        else {Commodity(commodity) for commodity in commodities}
    )

    workbook_path = Path(path)
    with _open_workbook(workbook_path) as workbook:
        worksheet = _price_sheet(workbook, workbook_path)
        return _read_prices(
            worksheet, workbook_path, start_month, end_month, selected_commodities
        )


@contextmanager
def _open_workbook(workbook_path: Path) -> Iterator[Workbook]:
    """Manage workbook resources and translate file/read failures during ingestion.

    Args:
        workbook_path (Path): Path to the local Pink Sheet XLSX workbook.

    Yields:
        Workbook: Workbook opened for ingestion and closed when the context exits.
    """
    try:
        with workbook_path.open("rb") as stream:
            workbook = load_workbook(stream, data_only=False, keep_links=False)
            try:
                yield workbook
            finally:
                workbook.close()
    except WorldBankDataError:
        raise
    except (
        OSError,
        BadZipFile,
        InvalidFileException,
        ParseError,
        KeyError,
        ValueError,
    ) as exc:
        raise WorldBankDataError(
            f"{workbook_path}: cannot read Pink Sheet workbook: {exc}"
        ) from exc


def _price_sheet(workbook: Workbook, path: Path) -> Worksheet:
    """Locate the monthly data sheet and check the minimum header layout.

    Args:
        workbook (Workbook): Opened Pink Sheet workbook.
        path (Path): Source path included in worksheet errors.

    Returns:
        Worksheet: Monthly data worksheet with the required header and unit rows.
    """
    if mappings.SHEET_NAME not in workbook.sheetnames:
        raise WorldBankDataError(f"{path}: missing worksheet {mappings.SHEET_NAME!r}")

    worksheet = workbook[mappings.SHEET_NAME]
    if not isinstance(worksheet, Worksheet) or worksheet.max_row < mappings.UNIT_ROW:
        raise WorldBankDataError(
            f"{path}: worksheet {mappings.SHEET_NAME!r}: missing header or unit rows"
        )

    return worksheet


def _series_columns(worksheet: Worksheet, context: str) -> dict[Commodity, int]:
    """Find all three series by name and validate units before filtering.

    Args:
        worksheet (Worksheet): Monthly data worksheet to inspect.
        context (str): Workbook and worksheet label used in schema errors.

    Returns:
        dict[Commodity, int]: Required commodities mapped to zero-based row-cell
            indices.
    """
    columns = {}
    for column_number, header in _iter_series_headers(worksheet):
        commodity = mappings.SERIES[header]
        if commodity in columns or column_number == mappings.MONTH_COLUMN:
            raise WorldBankDataError(
                f"{context}: row {mappings.HEADER_ROW}: "
                f"duplicate or misplaced {header!r}"
            )

        source_unit = worksheet.cell(row=mappings.UNIT_ROW, column=column_number).value
        _validate_price_unit(source_unit, header, context)
        columns[commodity] = column_number - 1
    _check_required_series(columns, context)

    return columns


def _iter_series_headers(worksheet: Worksheet) -> Iterator[tuple[int, str]]:
    """Yield column numbers and trimmed names for supported commodity headers.

    Args:
        worksheet (Worksheet): Monthly data worksheet whose header row is inspected.

    Yields:
        tuple[int, str]: One-based column number and trimmed supported series name for
            each matching header.
    """
    for column_number, cell in enumerate(worksheet[mappings.HEADER_ROW], start=1):
        if not isinstance(cell.value, str):
            continue

        header = cell.value.strip()
        if header in mappings.SERIES:
            yield column_number, header


def _check_required_series(columns: Mapping[Commodity, int], context: str) -> None:
    """Check that the discovered columns cover every required commodity series.

    Args:
        columns (Mapping[Commodity, int]): Discovered commodity series and their column
            indices.
        context (str): Workbook and worksheet label used in missing-series errors.

    Returns:
        None: No value is returned.
    """
    missing = set(mappings.SERIES.values()) - columns.keys()
    if missing:
        raise WorldBankDataError(
            f"{context}: row {mappings.HEADER_ROW}: missing commodity columns: "
            f"{', '.join(sorted(commodity.value for commodity in missing))}"
        )


def _validate_price_unit(source_unit: object, series_name: str, context: str) -> None:
    """Check a named series uses the verified USD-per-tonne unit label.

    Args:
        source_unit (object): Raw workbook cell containing the series unit.
        series_name (str): Supported series name included in unit errors.
        context (str): Workbook and worksheet label used in unit errors.

    Returns:
        None: No value is returned.
    """
    if not isinstance(source_unit, str) or source_unit.strip() != mappings.PRICE_UNIT:
        raise WorldBankDataError(
            f"{context}: row {mappings.UNIT_ROW}, {series_name}: "
            f"unexpected unit {source_unit!r}; expected {mappings.PRICE_UNIT!r}"
        )


def _read_prices(
    worksheet: Worksheet,
    path: Path,
    start_month: date | None,
    end_month: date | None,
    selected_commodities: set[Commodity],
) -> tuple[CommodityPrice, ...]:
    """Orchestrate schema validation, row selection, collection, and model creation.

    Args:
        worksheet (Worksheet): Monthly data worksheet to ingest.
        path (Path): Source workbook path used in observation context.
        start_month (date | None): Inclusive lower month bound, or None for no lower
            restriction.
        end_month (date | None): Inclusive upper month bound, or None for no upper
            restriction.
        selected_commodities (set[Commodity]): Commodities whose prices should be
            collected.

    Returns:
        tuple[CommodityPrice, ...]: Validated global prices sorted by month and
            commodity.
    """
    context = f"{path}: worksheet {mappings.SHEET_NAME!r}"
    columns = _series_columns(worksheet, context)
    selected_columns = {
        commodity: column
        for commodity, column in columns.items()
        if commodity in selected_commodities
    }
    rows = _selected_rows(worksheet, (start_month, end_month), context)
    observations = _collect_observations(rows, selected_columns)

    return _build_prices(observations)


def _selected_rows(
    worksheet: Worksheet,
    month_bounds: tuple[date | None, date | None],
    context: str,
) -> Iterator[_SelectedRow]:
    """Yield month, cells, and context after blank-row checks and month selection.

    Args:
        worksheet (Worksheet): Monthly data worksheet containing source rows.
        month_bounds (tuple[date | None, date | None]): Inclusive lower and upper month
            bounds, each optionally None.
        context (str): Workbook and worksheet label used in row errors.

    Yields:
        _SelectedRow: Selected month, raw row cells, and row-specific error context.
    """
    start_month, end_month = month_bounds
    for row_number, row in enumerate(
        worksheet.iter_rows(min_row=mappings.FIRST_DATA_ROW, values_only=True),
        start=mappings.FIRST_DATA_ROW,
    ):
        if all(
            cell is None or (isinstance(cell, str) and not cell.strip()) for cell in row
        ):
            continue

        row_context = f"{context}: row {row_number}"
        month = parsing.parse_month(row[mappings.MONTH_COLUMN - 1], row_context)
        if start_month is not None and month < start_month:
            continue

        if end_month is not None and month > end_month:
            continue

        yield month, row, row_context


def _collect_observations(
    rows: Iterable[_SelectedRow], columns: dict[Commodity, int]
) -> _Observations:
    """Parse selected series, skip missing cells, and enforce duplicate consistency.

    Args:
        rows (Iterable[_SelectedRow]): Selected months, raw cells, and row-specific
            error contexts.
        columns (dict[Commodity, int]): Selected commodities mapped to zero-based
            row-cell indices.

    Returns:
        _Observations: Parsed prices indexed by month and commodity, with equal
            duplicates collapsed.
    """
    observations: _Observations = {}
    for month, row, row_context in rows:
        for commodity, column in columns.items():
            observation_context = f"{row_context}, {commodity.value}, {month:%Y-%m}"
            price = parsing.parse_price(row[column], observation_context)
            if price is not None:
                _record_price(
                    observations, (month, commodity), price, observation_context
                )

    return observations


def _build_prices(observations: _Observations) -> tuple[CommodityPrice, ...]:
    """Construct immutable price models sorted by month and commodity identifier.

    Args:
        observations (_Observations): Validated decimal and float prices indexed by
            month and commodity.

    Returns:
        tuple[CommodityPrice, ...]: Immutable global reference-price models sorted by
            month and commodity.
    """
    return tuple(
        CommodityPrice(
            month=month,
            commodity=commodity,
            price_usd_per_tonne=price.usd_per_tonne,
            data_source=mappings.DATA_SOURCE,
        )
        for (month, commodity), price in sorted(observations.items())
    )


def _record_price(
    observations: _Observations,
    key: _PriceKey,
    price: parsing.ParsedPrice,
    context: str,
) -> None:
    """Retain equivalent duplicates once, comparing decimals before float conversion.

    Args:
        observations (_Observations): Parsed prices updated in place.
        key (_PriceKey): Month and commodity identifying the observation.
        price (parsing.ParsedPrice): Exact source price and finite float representation.
        context (str): Observation label used in duplicate-conflict errors.

    Returns:
        None: No value is returned.
    """
    previous = observations.get(key)
    if previous is not None and previous.source_value != price.source_value:
        raise WorldBankDataError(f"{context}: conflicting duplicate price")
    observations[key] = price
