"""Verified PSD codes, descriptions, and CSV requirements used by the loader.

Only standard soybean commodities and the existing European Union aggregate are
mapped. Local variants and EU-15 are excluded rather than summed or backfilled.
The unit constants describe the single physical quantity unit currently supported.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from soybean_sim.models import Commodity, Region

COMMODITIES: Final[Mapping[str, tuple[Commodity, str]]] = MappingProxyType(
    {
        "2222000": (Commodity.SOYBEANS, "Oilseed, Soybean"),
        "0813100": (Commodity.SOYBEAN_MEAL, "Meal, Soybean"),
        "4232000": (Commodity.SOYBEAN_OIL, "Oil, Soybean"),
    }
)

REGIONS: Final[Mapping[str, tuple[Region, str]]] = MappingProxyType(
    {
        "US": (Region.UNITED_STATES, "United States"),
        "BR": (Region.BRAZIL, "Brazil"),
        "AR": (Region.ARGENTINA, "Argentina"),
        "E4": (Region.EUROPEAN_UNION, "European Union"),
    }
)

ATTRIBUTES: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(
    {
        "028": ("production_tonnes", "Production"),
        "057": ("imports_tonnes", "Imports"),
        "088": ("exports_tonnes", "Exports"),
        "125": ("domestic_consumption_tonnes", "Domestic Consumption"),
        "020": ("beginning_stocks_tonnes", "Beginning Stocks"),
        "176": ("ending_stocks_tonnes", "Ending Stocks"),
        "007": ("crush_tonnes", "Crush"),
    }
)

REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "Commodity_Code",
        "Commodity_Description",
        "Country_Code",
        "Country_Name",
        "Market_Year",
        "Attribute_ID",
        "Attribute_Description",
        "Unit_ID",
        "Unit_Description",
        "Value",
    }
)

QUANTITY_UNIT_ID: Final = "08"
QUANTITY_UNIT_DESCRIPTION: Final = "(1000 MT)"
TONNES_PER_SOURCE_UNIT: Final = 1000
