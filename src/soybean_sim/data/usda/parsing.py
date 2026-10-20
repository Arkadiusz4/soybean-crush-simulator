import math
import re
from datetime import MAXYEAR, MINYEAR
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

_YEAR_LABEL_PATTERN = re.compile(r"[0-9]{4}")


class USDADataError(ValueError):
    """The local USDA CSV could not be read or contains invalid observations."""


class _ParsedQuantity(NamedTuple):
    """Pair an exact source decimal in 1000 MT with float tonnes; blanks become None."""

    source_value: Decimal | None
    tonnes: float | None


def _resolve_year_bounds(
    marketing_year: str | None, start_year: int | None, end_year: int | None
) -> tuple[int, int]:
    """Resolve one four-digit marketing-year label or inclusive integer year bounds.

    Use either a single year or ordered range bounds, defaulting to 1 and 9999.
    """

    if marketing_year is not None and (
        start_year is not None
        or end_year is not None
        or not isinstance(marketing_year, str)
        or not _YEAR_LABEL_PATTERN.fullmatch(marketing_year)
    ):
        raise ValueError(
            "marketing_year cannot be combined with year range bounds"
            if start_year is not None or end_year is not None
            else "marketing_year must be a four-digit year string"
        )

    if marketing_year is not None:
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


def _parse_marketing_year(value: str, source_context: str) -> str:
    """Validate and return the unchanged nonzero four-digit marketing-year label."""

    if not _YEAR_LABEL_PATTERN.fullmatch(value) or int(value) == 0:
        raise USDADataError(f"{source_context}: invalid Market_Year {value!r}")

    return value


def _parse_quantity(value_text: str, source_context: str) -> _ParsedQuantity:
    """Convert a trimmed 1000 MT cell to exact source decimals and float tonnes.

    Blank cells return two None values. Quantities must be finite and nonnegative,
    remaining representable after conversion without turning nonzero values to zero.
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
    """Validate a selected row's descriptions and unit, then convert its quantity.

    Expect trimmed cells with supported codes already selected by the loader.
    Only unit 08 / (1000 MT) is accepted. Return source decimals and metric tonnes,
    keeping blanks missing.
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
