"""Validate selected PSD observations and convert source quantities to metric tonnes."""

import math
import re
from datetime import MAXYEAR, MINYEAR
from decimal import Decimal, DecimalException, InvalidOperation
from typing import Final, NamedTuple

from . import _mappings as mappings
from ._errors import USDADataError as USDADataError
from ._errors import USDAObservationError

_YEAR_LABEL_PATTERN: Final = re.compile(r"[0-9]{4}")


class ParsedQuantity(NamedTuple):
    """Pair an exact source decimal in 1000 MT with float tonnes; blanks become None.

    Args:
        source_value (Decimal | None): Exact quantity in source units of 1000 MT, or
            None for a blank cell.
        tonnes (float | None): Converted metric tonnes, or None for a blank cell.
    """

    source_value: Decimal | None
    tonnes: float | None


def resolve_year_bounds(
    marketing_year: str | None, start_year: int | None, end_year: int | None
) -> tuple[int, int]:
    """Resolve one four-digit marketing-year label or inclusive integer year bounds.

    Use either a single year or ordered range bounds, defaulting to 1 and 9999.

    Args:
        marketing_year (str | None): Single four-digit year label, mutually exclusive
            with range bounds.
        start_year (int | None): Inclusive lower year bound, or None to use the minimum
            supported year.
        end_year (int | None): Inclusive upper year bound, or None to use the maximum
            supported year.

    Returns:
        tuple[int, int]: Inclusive lower and upper marketing-year bounds.
    """
    if marketing_year is not None:
        if start_year is not None or end_year is not None:
            raise ValueError("marketing_year cannot be combined with year range bounds")
        if not isinstance(marketing_year, str) or not _YEAR_LABEL_PATTERN.fullmatch(
            marketing_year
        ):
            raise ValueError("marketing_year must be a four-digit year string")
        start_year = end_year = int(marketing_year)

    for bound_name, bound_value in (("start_year", start_year), ("end_year", end_year)):
        if bound_value is None:
            continue
        valid_bound = type(bound_value) is int and MINYEAR <= bound_value <= MAXYEAR

        if not valid_bound:
            raise ValueError(
                f"{bound_name} must be an integer between {MINYEAR} and {MAXYEAR}"
            )

    lower_bound = MINYEAR if start_year is None else start_year
    upper_bound = MAXYEAR if end_year is None else end_year

    if lower_bound > upper_bound:
        raise ValueError("start_year must not exceed end_year")

    return lower_bound, upper_bound


def parse_marketing_year(value: str, source_context: str) -> str:
    """Validate and return the unchanged nonzero four-digit marketing-year label.

    Args:
        value (str): Source marketing-year label to validate.
        source_context (str): File and row label used in validation errors.

    Returns:
        str: Unchanged nonzero four-digit marketing-year label.
    """
    if not _YEAR_LABEL_PATTERN.fullmatch(value) or int(value) == 0:
        raise USDAObservationError(f"{source_context}: invalid Market_Year {value!r}")

    return value


def _parse_quantity(value_text: str, source_context: str) -> ParsedQuantity:
    """Convert a trimmed 1000 MT cell to exact source decimals and float tonnes.

    Blank cells return two None values. Quantities must be finite and nonnegative,
    remaining representable after conversion without turning nonzero values to zero.

    Args:
        value_text (str): Trimmed quantity cell in source units of 1000 MT.
        source_context (str): File and row label used in validation errors.

    Returns:
        ParsedQuantity: Exact source quantity and converted metric tonnes; both fields
            are None for a blank cell.
    """
    if not value_text:
        return ParsedQuantity(source_value=None, tonnes=None)
    try:
        # Decimal accepts underscores, which are not a PSD numeric representation.
        if "_" in value_text:
            raise InvalidOperation
        source_value = Decimal(value_text)
    except InvalidOperation as exc:
        raise USDAObservationError(
            f"{source_context}: malformed Value {value_text!r}"
        ) from exc

    if not source_value.is_finite() or source_value < 0:
        raise USDAObservationError(
            f"{source_context}: Value must be finite and nonnegative: {value_text!r}"
        )
    try:
        tonnes = float(source_value * mappings.TONNES_PER_SOURCE_UNIT)
    except DecimalException as exc:
        raise USDAObservationError(
            f"{source_context}: Value cannot be represented in metric tonnes"
        ) from exc
    if not math.isfinite(tonnes) or (source_value != 0 and tonnes == 0):
        raise USDAObservationError(
            f"{source_context}: Value cannot be represented in metric tonnes"
        )

    return ParsedQuantity(source_value=source_value, tonnes=tonnes)


def parse_observation(row: dict[str, str], source_context: str) -> ParsedQuantity:
    """Validate a selected row's descriptions and unit, then convert its quantity.

    Expect trimmed cells with supported codes already selected by the loader.
    Only unit 08 / (1000 MT) is accepted. Return source decimals and metric tonnes,
    keeping blanks missing.

    Args:
        row (dict[str, str]): Trimmed CSV cells with supported commodity, region, and
            attribute codes.
        source_context (str): File and row label used in validation errors.

    Returns:
        ParsedQuantity: Validated source quantity and metric tonnes, preserving missing
            values.
    """
    _, commodity_description = mappings.COMMODITIES[row["Commodity_Code"]]
    _, country_name = mappings.REGIONS[row["Country_Code"]]
    _, attribute_description = mappings.ATTRIBUTES[row["Attribute_ID"]]

    expected_descriptions = {
        "Commodity_Description": commodity_description,
        "Country_Name": country_name,
        "Attribute_Description": attribute_description,
    }

    for column_name, expected_description in expected_descriptions.items():
        actual_description = row[column_name]
        if actual_description != expected_description:
            raise USDAObservationError(
                f"{source_context}: inconsistent {column_name}: "
                f"expected {expected_description!r}, got {actual_description!r}"
            )

    source_unit_id = row["Unit_ID"]
    source_unit_description = row["Unit_Description"]
    if (
        source_unit_id != mappings.QUANTITY_UNIT_ID
        or source_unit_description != mappings.QUANTITY_UNIT_DESCRIPTION
    ):
        raise USDAObservationError(
            f"{source_context}: unsupported or inconsistent unit "
            f"{source_unit_id!r} / {source_unit_description!r}; "
            f"expected {mappings.QUANTITY_UNIT_ID!r} / "
            f"{mappings.QUANTITY_UNIT_DESCRIPTION!r}"
        )

    return _parse_quantity(row["Value"], source_context)
