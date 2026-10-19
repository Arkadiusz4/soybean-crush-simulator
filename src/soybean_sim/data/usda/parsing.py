"""Validate USDA year labels, observation metadata, and physical quantities.

This module performs no file I/O or record aggregation. Selected rows are checked
against verified descriptions and units before their values are converted. Exact
source decimals are retained alongside model-ready tonnes for duplicate checks.
"""

import math
import re
from decimal import Decimal, DecimalException, InvalidOperation
from typing import NamedTuple

from soybean_sim.data.usda.mappings import (
    _ATTRIBUTES,
    _COMMODITIES,
    _QUANTITY_UNIT_DESCRIPTION,
    _QUANTITY_UNIT_ID,
    _REGIONS,
    _TONNES_PER_SOURCE_UNIT,
)

_MIN_VALID_YEAR = 1
_MAX_VALID_YEAR = 9999
_YEAR_LABEL_PATTERN = re.compile(r"[0-9]{4}")


class USDADataError(ValueError):
    """The local USDA CSV could not be read or contains invalid observations."""


class _ParsedQuantity(NamedTuple):
    """Keep duplicate-comparison precision separate from the model's float value.

    Attributes:
        source_value: Original Decimal in thousands of metric tonnes, or None
            when the source cell is blank. Used for exact duplicate comparisons.
        tonnes: Validated float in metric tonnes, or None for a blank cell.
            Conversion happens once, before constructing a MarketBalance.
    """

    source_value: Decimal | None
    tonnes: float | None


def _resolve_year_bounds(
    marketing_year: str | None, start_year: int | None, end_year: int | None
) -> tuple[int, int]:
    """Resolve the caller's year selection into validated inclusive bounds.

    Args:
        marketing_year: A four-digit year label, mutually exclusive with bounds.
        start_year: Optional inclusive lower bound; defaults to 1.
        end_year: Optional inclusive upper bound; defaults to 9999.

    Returns:
        Inclusive integer bounds used for filtering source marketing years.

    Raises:
        ValueError: Selectors conflict, a label is malformed, a bound is not an
            integer from 1 through 9999, or the lower bound exceeds the upper.
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
        valid_bound = (
            type(bound_value) is int
            and _MIN_VALID_YEAR <= bound_value <= _MAX_VALID_YEAR
        )

        if not valid_bound:
            raise ValueError(
                f"{bound_name} must be an integer between "
                f"{_MIN_VALID_YEAR} and {_MAX_VALID_YEAR}"
            )

    lower_bound = _MIN_VALID_YEAR if start_year is None else start_year
    upper_bound = _MAX_VALID_YEAR if end_year is None else end_year

    if lower_bound > upper_bound:
        raise ValueError("start_year must not exceed end_year")

    return lower_bound, upper_bound


def _parse_marketing_year(value: str, source_context: str) -> str:
    """Validate a source year label without turning it into a calendar period.

    Returns:
        The unchanged four-digit label, whose numeric value must be nonzero.

    Raises:
        USDADataError: The label is invalid. The message includes source_context,
            which identifies the CSV path and row supplied by the loader.
    """

    if not _YEAR_LABEL_PATTERN.fullmatch(value) or int(value) == 0:
        raise USDADataError(f"{source_context}: invalid Market_Year {value!r}")

    return value


def _parse_quantity(value_text: str, source_context: str) -> _ParsedQuantity:
    """Parse a source quantity while preserving missing values and decimal precision.

    Args:
        value_text: Trimmed Value cell, in the verified thousands-of-tonnes unit.
        source_context: CSV path and row number for validation errors.

    Returns:
        Original decimal and converted tonnes, or two None values for a blank
        cell. Numbers must be finite and nonnegative. Conversion must neither
        overflow nor turn a nonzero quantity into zero.

    Raises:
        USDADataError: The number is malformed or violates these numeric rules.
    """

    if not value_text:
        return _ParsedQuantity(source_value=None, tonnes=None)
    try:
        # Decimal accepts underscores, which are not a PSD numeric representation.
        if "_" in value_text:
            raise InvalidOperation
        source_value = Decimal(value_text)
    except InvalidOperation as exc:
        raise USDADataError(
            f"{source_context}: malformed Value {value_text!r}"
        ) from exc
    
    if not source_value.is_finite() or source_value < 0:
        raise USDADataError(
            f"{source_context}: Value must be finite and nonnegative: {value_text!r}"
        )
    try:
        tonnes = float(source_value * _TONNES_PER_SOURCE_UNIT)
    except DecimalException as exc:
        raise USDADataError(
            f"{source_context}: Value cannot be represented in metric tonnes"
        ) from exc
    if not math.isfinite(tonnes) or (source_value != 0 and tonnes == 0):
        raise USDADataError(
            f"{source_context}: Value cannot be represented in metric tonnes"
        )

    return _ParsedQuantity(source_value=source_value, tonnes=tonnes)


def _parse_observation(row: dict[str, str], source_context: str) -> _ParsedQuantity:
    """Validate a selected row's metadata, then parse its quantity.

    The loader must first select supported codes, attributes, and years. Matching
    code/description pairs guard against source schema changes. Only the verified
    physical unit is converted; rate and area attributes are filtered elsewhere.

    Args:
        row: Whitespace-normalized CSV row with supported identifiers.
        source_context: CSV path and row number for validation errors.

    Returns:
        The source decimal and model-ready metric tonnes, keeping blanks missing.

    Raises:
        USDADataError: A description, unit, or numeric value is inconsistent.
    """

    _, commodity_description = _COMMODITIES[row["Commodity_Code"]]
    _, country_name = _REGIONS[row["Country_Code"]]
    _, attribute_description = _ATTRIBUTES[row["Attribute_ID"]]

    expected_descriptions = {
        "Commodity_Description": commodity_description,
        "Country_Name": country_name,
        "Attribute_Description": attribute_description,
    }

    for column_name, expected_description in expected_descriptions.items():
        actual_description = row[column_name]
        if actual_description != expected_description:
            raise USDADataError(
                f"{source_context}: inconsistent {column_name}: "
                f"expected {expected_description!r}, got {actual_description!r}"
            )

    source_unit_id = row["Unit_ID"]
    source_unit_description = row["Unit_Description"]
    if (
        source_unit_id != _QUANTITY_UNIT_ID
        or source_unit_description != _QUANTITY_UNIT_DESCRIPTION
    ):
        raise USDADataError(
            f"{source_context}: unsupported or inconsistent unit "
            f"{source_unit_id!r} / {source_unit_description!r}; "
            f"expected {_QUANTITY_UNIT_ID!r} / {_QUANTITY_UNIT_DESCRIPTION!r}"
        )

    return _parse_quantity(row["Value"], source_context)
