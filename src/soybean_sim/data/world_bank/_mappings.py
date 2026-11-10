"""Describe the verified Pink Sheet layout, soybean series, and source units."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from soybean_sim.models import Commodity

SHEET_NAME: Final = "Monthly Prices"
HEADER_ROW: Final = 5
UNIT_ROW: Final = 6
FIRST_DATA_ROW: Final = 7
MONTH_COLUMN: Final = 1
PRICE_UNIT: Final = "($/mt)"
DATA_SOURCE: Final = "World Bank Pink Sheet"
MISSING_MARKERS: Final = frozenset({"", "...", "…"})
SERIES: Final[Mapping[str, Commodity]] = MappingProxyType(
    {
        "Soybeans": Commodity.SOYBEANS,
        "Soybean oil": Commodity.SOYBEAN_OIL,
        "Soybean meal": Commodity.SOYBEAN_MEAL,
    }
)
