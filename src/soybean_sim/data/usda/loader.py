import csv
from collections.abc import Iterable, Sequence
from pathlib import Path

from soybean_sim.models import Commodity, MarketBalance, Region

from .errors import (
    USDAFileError,
    USDAFormatError,
    USDAObservationError,
)
from .mappings import (
    _ATTRIBUTES,
    _COMMODITIES,
    _REGIONS,
    _REQUIRED_COLUMNS,
)
from .parsing import (
    _parse_marketing_year,
    _parse_observation,
    _ParsedQuantity,
    _resolve_year_bounds,
)

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
    """Load a local UTF-8 PSD CSV, sorting balances by region, commodity, and year.

    Select one four-digit marketing_year or inclusive start_year/end_year bounds.
    Omitted bounds are unrestricted. Region and commodity filters default to all;
    empty filters select none. A UTF-8 byte-order mark is accepted.

    Convert only unit 08 / (1000 MT) to tonnes. Missing quantities remain None,
    equivalent duplicates count once, and no matches return an empty tuple.
    Annual quantities are never allocated to months.
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
        raise USDAFileError(f"Cannot read USDA PSD CSV '{csv_path}': {exc}") from exc
    except csv.Error as exc:
        raise USDAFormatError(f"{csv_path}: malformed CSV: {exc}") from exc

    return _build_balances(grouped_observations)


def _validate_columns(columns: Sequence[str], csv_path: Path) -> None:
    """Check that required headers exist and all column names are nonempty and unique.

    Check missing names first; allow unrelated extra columns without renaming them.
    """

    missing_columns = _REQUIRED_COLUMNS - set(columns)
    if missing_columns:
        raise USDAFormatError(
            f"{csv_path}: missing required columns: "
            f"{', '.join(sorted(missing_columns))}"
        )

    if len(columns) != len(set(columns)) or any(not name for name in columns):
        raise USDAFormatError(f"{csv_path}: duplicate or empty column names")


def _read_observations(
    reader: csv.DictReader,
    csv_path: Path,
    year_bounds: tuple[int, int],
    selected_regions: set[Region],
    selected_commodities: set[Commodity],
) -> _GroupedBalances:
    """Normalize, filter, parse, and group rows by region, commodity, and source year.

    The reader must have a validated header. Year bounds are inclusive. Retain exact
    source decimals for duplicate comparisons alongside converted tonnes.
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
    """Check row width and strip surrounding whitespace from each cell."""
    if None in row or any(value is None for value in row.values()):
        raise USDAFormatError(f"{source_context}: row length does not match CSV header")
    return {column_name: value.strip() for column_name, value in row.items()}


def _select_observation(
    row: dict[str, str],
    source_context: str,
    year_bounds: tuple[int, int],
    selected_regions: set[Region],
    selected_commodities: set[Commodity],
) -> tuple[_BalanceKey, str] | None:
    """Return the annual balance key and field name, or None for an excluded row.

    Filter commodity/region codes, then attributes, then inclusive year bounds.
    Ignore meal/oil crush before parsing years and verify recognized attribute IDs
    before year filtering. Other metadata and values are parsed later.
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
            raise USDAObservationError(
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
    """Store an attribute in place, comparing source decimals rather than floats.

    Equivalent duplicates count once. None stays distinct from a known quantity
    when checking for conflicting observations.
    """

    attribute_quantities = grouped_observations.setdefault(balance_key, {})
    previous_quantity = attribute_quantities.get(field_name)
    if (
        previous_quantity is not None
        and previous_quantity.source_value != quantity.source_value
    ):
        region, commodity, marketing_year = balance_key
        raise USDAObservationError(
            f"{source_context}: conflicting duplicate for {region.value}, "
            f"{commodity.value}, {marketing_year}, {field_name}"
        )
    attribute_quantities[field_name] = quantity


def _build_balances(
    grouped_observations: _GroupedBalances,
) -> tuple[MarketBalance, ...]:
    """Build annual balances sorted by region, commodity, and source year label.

    Absent attributes become None; recorded zeroes remain zero. Quantities are
    already in tonnes and are not imputed or reconciled here.
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
