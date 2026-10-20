"""Orchestrate local PSD CSV ingestion without changing annual reporting periods.

Rows are normalized, selected, parsed, and grouped in that order. Duplicate
observations are compared using original decimals; output models use converted
metric tonnes. File and CSV errors are reported with their source path.
"""

import csv
from collections.abc import Iterable, Sequence
from pathlib import Path

from soybean_sim.data.usda.mappings import (
    _ATTRIBUTES,
    _COMMODITIES,
    _REGIONS,
    _REQUIRED_COLUMNS,
)
from soybean_sim.data.usda.parsing import (
    USDADataError,
    _parse_marketing_year,
    _parse_observation,
    _ParsedQuantity,
    _resolve_year_bounds,
)
from soybean_sim.models import Commodity, MarketBalance, Region

_BalanceKey = tuple[Region, Commodity, str]
_GroupedBalances = dict[_BalanceKey, dict[str, _ParsedQuantity]]


def load_usda_psd_csv(
    path: str | Path,
    *,
    marketing_year: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    regions: Iterable[Region] | None = None,
    commodities: Iterable[Commodity] | None = None,
) -> tuple[MarketBalance, ...]:
    """Load annual market balances from a local UTF-8 USDA PSD Oilseeds CSV.

    Args:
        path: Local CSV path; a UTF-8 byte-order mark is accepted.
        marketing_year: One four-digit year label, instead of range bounds.
        start_year: Inclusive lower bound; omitted means no lower restriction.
        end_year: Inclusive upper bound; omitted means no upper restriction.
        regions: Selected model regions. None selects all; an empty iterable none.
        commodities: Selected model commodities, with the same selection rules.

    Returns:
        Balances sorted by region identifier, commodity identifier, then year.
        Blank or absent quantities remain None. Only unit 08 / (1000 MT) is
        supported. Identical duplicates are retained once; no matches return an
        empty tuple. Annual quantities are never allocated to months.

    Raises:
        ValueError: Year selectors or region/commodity filters are invalid.
        USDADataError: Reading fails or selected data is invalid, including
            conflicting duplicates. Messages retain file and applicable row context.
    """

    year_bounds = _resolve_year_bounds(marketing_year, start_year, end_year)
    selected_regions = (
        set(Region) if regions is None else {Region(region) for region in regions}
    )
    selected_commodities = (
        set(Commodity)
        if commodities is None
        else {Commodity(commodity) for commodity in commodities}
    )
    csv_path = Path(path)

    try:
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            _validate_columns(reader.fieldnames or [], csv_path)
            grouped_observations = _read_observations(
                reader, csv_path, year_bounds, selected_regions, selected_commodities
            )
    except (OSError, UnicodeError) as exc:
        raise USDADataError(f"Cannot read USDA PSD CSV '{csv_path}': {exc}") from exc
    except csv.Error as exc:
        raise USDADataError(f"{csv_path}: malformed CSV: {exc}") from exc

    return _build_balances(grouped_observations)


def _validate_columns(columns: Sequence[str], csv_path: Path) -> None:
    """Check required columns before consuming rows; allow unrelated extra columns.

    Raises:
        USDADataError: Required names are missing, or any header name is empty
            or duplicated. Missing-column errors take precedence over duplicates.
    """

    missing_columns = _REQUIRED_COLUMNS - set(columns)
    if missing_columns:
        raise USDADataError(
            f"{csv_path}: missing required columns: "
            f"{', '.join(sorted(missing_columns))}"
        )

    if len(columns) != len(set(columns)) or any(not name for name in columns):
        raise USDADataError(f"{csv_path}: duplicate or empty column names")


def _read_observations(
    reader: csv.DictReader,
    csv_path: Path,
    year_bounds: tuple[int, int],
    selected_regions: set[Region],
    selected_commodities: set[Commodity],
) -> _GroupedBalances:
    """Run the row pipeline and collect selected observations into annual groups.

    Args:
        reader: CSV reader positioned after its validated header.
        csv_path: Source path used with the reader's physical line number in errors.
        year_bounds: Inclusive integer bounds for marketing-year selection.
        selected_regions: Model regions to retain, after source-code mapping.
        selected_commodities: Model commodities to retain, excluding local variants.

    Returns:
        Groups keyed by region, commodity, and unchanged marketing-year label.
        Each attribute retains both its source decimal and converted tonnes.

    Raises:
        USDADataError: A row is malformed, a selected observation is invalid,
            or repeated observations conflict.
    """

    grouped_observations: _GroupedBalances = {}

    for raw_row in reader:
        source_context = f"{csv_path}: row {reader.line_num}"
        row = _normalize_csv_row(raw_row, source_context)
        selection = _select_observation(
            row, source_context, year_bounds, selected_regions, selected_commodities
        )

        if selection is None:
            continue

        balance_key, field_name = selection
        quantity = _parse_observation(row, source_context)
        _record_observation(
            grouped_observations, balance_key, field_name, quantity, source_context
        )

    return grouped_observations


def _normalize_csv_row(row: dict[str, str], source_context: str) -> dict[str, str]:
    """Reject rows with missing/extra cells, then strip surrounding whitespace.

    CSV readers report broken row widths with a None key or value. Check this
    before filtering so malformed CSV structure is never silently ignored.

    Returns:
        A row whose cell values have surrounding whitespace removed.

    Raises:
        USDADataError: The row length does not match the CSV header.
    """
    if None in row or any(value is None for value in row.values()):
        raise USDADataError(f"{source_context}: row length does not match CSV header")
    return {column_name: value.strip() for column_name, value in row.items()}


def _select_observation(
    row: dict[str, str],
    source_context: str,
    year_bounds: tuple[int, int],
    selected_regions: set[Region],
    selected_commodities: set[Commodity],
) -> tuple[_BalanceKey, str] | None:
    """Select supported annual attributes without parsing their numeric values.

    Code filters precede attribute checks, and attribute checks precede source-year
    validation. Preserve this order: ignored meal/oil crush rows need no valid year,
    while a recognized attribute with a wrong ID must fail even outside the range.

    Returns:
        The annual balance key and model field name, or None for an excluded row.
        Descriptions, units, and quantities are validated only after selection.

    Raises:
        USDADataError: A recognized attribute description has an unsupported ID,
            or a relevant row has an invalid marketing-year label.
    """

    commodity_mapping = _COMMODITIES.get(row["Commodity_Code"])
    region_mapping = _REGIONS.get(row["Country_Code"])
    if commodity_mapping is None or region_mapping is None:
        return None

    commodity, _ = commodity_mapping
    region, _ = region_mapping
    if commodity not in selected_commodities or region not in selected_regions:
        return None

    attribute_mapping = _ATTRIBUTES.get(row["Attribute_ID"])

    if attribute_mapping is None:
        known_descriptions = {description for _, description in _ATTRIBUTES.values()}

        if row["Attribute_Description"] in known_descriptions:
            raise USDADataError(
                f"{source_context}: unsupported Attribute_ID for "
                f"{row['Attribute_Description']!r}"
            )

        return None

    field_name, _ = attribute_mapping
    if field_name == "crush_tonnes" and commodity != Commodity.SOYBEANS:
        return None

    marketing_year = _parse_marketing_year(row["Market_Year"], source_context)
    lower_bound, upper_bound = year_bounds
    if not lower_bound <= int(marketing_year) <= upper_bound:
        return None

    return (region, commodity, marketing_year), field_name


def _record_observation(
    grouped_observations: _GroupedBalances,
    balance_key: _BalanceKey,
    field_name: str,
    quantity: _ParsedQuantity,
    source_context: str,
) -> None:
    """Store one attribute, rejecting conflicting duplicates without summing values.

    The groups are updated in place. Compare original decimals to avoid float
    rounding concealing conflicts. Equivalent values are accepted; None versus
    a known value is a conflict rather than an instruction to fill a missing cell.

    Raises:
        USDADataError: The same annual attribute already has a different value.
    """

    attribute_quantities = grouped_observations.setdefault(balance_key, {})
    previous_quantity = attribute_quantities.get(field_name)
    if (
        previous_quantity is not None
        and previous_quantity.source_value != quantity.source_value
    ):
        region, commodity, marketing_year = balance_key
        raise USDADataError(
            f"{source_context}: conflicting duplicate for {region.value}, "
            f"{commodity.value}, {marketing_year}, {field_name}"
        )
    attribute_quantities[field_name] = quantity


def _build_balances(
    grouped_observations: _GroupedBalances,
) -> tuple[MarketBalance, ...]:
    """Construct annual models from converted quantities in deterministic order.

    Returns:
        One MarketBalance per group, sorted by region, commodity, and year label.
        Attributes absent from a group become None, while recorded zeroes remain
        zero. No conversion, imputation, or accounting reconciliation occurs here.
    """

    balances = []
    for balance_key in sorted(grouped_observations):
        region, commodity, marketing_year = balance_key
        attribute_quantities = grouped_observations[balance_key]
        quantities_in_tonnes = {}

        for field_name, _ in _ATTRIBUTES.values():
            quantity = attribute_quantities.get(field_name)
            quantities_in_tonnes[field_name] = (
                None if quantity is None else quantity.tonnes
            )

        balances.append(
            MarketBalance(
                region=region,
                commodity=commodity,
                marketing_year=marketing_year,
                **quantities_in_tonnes,
            )
        )

    return tuple(balances)
