"""Validate calendar months and positive nominal USD-per-tonne observations."""

import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final, NamedTuple

from . import mappings

_MONTH_PATTERN: Final = re.compile(r"([0-9]{4})M([0-9]{2})")


class WorldBankDataError(ValueError):
    """A local Pink Sheet workbook cannot be read or contains invalid price data."""


class ParsedPrice(NamedTuple):
    """Retain decimal precision for duplicates alongside the model's float price."""

    source_value: Decimal
    usd_per_tonne: float


def validate_month_range(start_month: date | None, end_month: date | None) -> None:
    """Check inclusive first-of-month boundaries without accepting datetimes."""
    if start_month is not None and end_month is not None and start_month > end_month:
        raise ValueError("start_month must not exceed end_month")

    for name, boundary in (("start_month", start_month), ("end_month", end_month)):
        if boundary is None:
            continue

        if type(boundary) is not date or boundary.day != 1:
            raise ValueError(f"{name} must be a date on the first day of a month")


def parse_month(source_month: object, source_context: str) -> date:
    """Convert a YYYYMmm source label to the first day of that calendar month."""
    match = (
        _MONTH_PATTERN.fullmatch(source_month.strip())
        if isinstance(source_month, str)
        else None
    )

    if match is None:
        raise WorldBankDataError(
            f"{source_context}: invalid month identifier {source_month!r}; "
            "expected YYYYMmm"
        )
    try:
        return date(int(match[1]), int(match[2]), 1)
    except ValueError as exc:
        raise WorldBankDataError(
            f"{source_context}: invalid month identifier {source_month!r}"
        ) from exc


def parse_price(source_price: object, source_context: str) -> ParsedPrice | None:
    """Parse a positive USD/tonne value; skip blanks and verified ellipsis markers."""
    if source_price is None:
        return None

    if (
        isinstance(source_price, str)
        and source_price.strip() in mappings.MISSING_MARKERS
    ):
        return None

    source_value = _parse_decimal_price(source_price, source_context)
    price = _to_model_price(source_value, source_context)

    return ParsedPrice(source_value=source_value, usd_per_tonne=price)


def _parse_decimal_price(source_price: object, source_context: str) -> Decimal:
    """Validate a supported source value and retain its positive decimal precision."""
    if isinstance(source_price, bool) or not isinstance(
        source_price, (int, float, str)
    ):
        raise WorldBankDataError(f"{source_context}: invalid price {source_price!r}")
    price_text = str(source_price).strip()

    try:
        if "_" in price_text:
            raise InvalidOperation
        source_value = Decimal(price_text)
    except InvalidOperation as exc:
        raise WorldBankDataError(
            f"{source_context}: malformed price {source_price!r}"
        ) from exc

    if not source_value.is_finite() or source_value <= 0:
        raise WorldBankDataError(
            f"{source_context}: price must be finite and positive: {source_price!r}"
        )

    return source_value


def _to_model_price(source_value: Decimal, source_context: str) -> float:
    """Convert a validated decimal to a positive finite float for CommodityPrice."""
    price = float(source_value)
    if not math.isfinite(price) or price <= 0:
        raise WorldBankDataError(
            f"{source_context}: price cannot be represented in USD per metric tonne"
        )

    return price
