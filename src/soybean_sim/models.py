"""Shared identifiers for the supported regions and commodities."""

from enum import StrEnum


class Region(StrEnum):
    """Regions in the planned model; values are stable configuration identifiers."""

    UNITED_STATES = "united_states"
    BRAZIL = "brazil"
    ARGENTINA = "argentina"
    EUROPEAN_UNION = "european_union"


class Commodity(StrEnum):
    """Commodities in the planned model."""

    SOYBEANS = "soybeans"
    SOYBEAN_MEAL = "soybean_meal"
    SOYBEAN_OIL = "soybean_oil"
