import json

import pytest

from soybean_sim.models import Commodity, Region


def test_supported_regions() -> None:
    """Check the four stable region identifiers and conversion from a string value."""
    assert {region.value for region in Region} == {
        "united_states",
        "brazil",
        "argentina",
        "european_union",
    }
    assert Region("brazil") is Region.BRAZIL


def test_commodities_serialize_as_strings() -> None:
    """Check that commodity enums serialize to their stable JSON string identifiers."""
    assert json.loads(json.dumps(list(Commodity))) == [
        "soybeans",
        "soybean_meal",
        "soybean_oil",
    ]


@pytest.mark.parametrize("enum_type", [Region, Commodity])
def test_unknown_identifier_is_rejected(enum_type: type[Region | Commodity]) -> None:
    """Check that neither domain enum accepts an unsupported string identifier."""
    with pytest.raises(ValueError):
        enum_type("unknown")
