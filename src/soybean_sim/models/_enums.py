"""Define stable region and commodity identifiers shared by settings and records."""

from enum import StrEnum


class Region(StrEnum):
    """Identify the four supported regions in settings and serialized records.

    The European Union is represented as one region, rather than member states.
    """

    UNITED_STATES = "united_states"
    BRAZIL = "brazil"
    ARGENTINA = "argentina"
    EUROPEAN_UNION = "european_union"


class Commodity(StrEnum):
    """Identify soybeans and their two modeled processing products: meal and oil."""

    SOYBEANS = "soybeans"
    SOYBEAN_MEAL = "soybean_meal"
    SOYBEAN_OIL = "soybean_oil"
