import json

import pytest

from soybean_sim.models import Commodity, Region


def test_supported_regions() -> None:
    assert {region.value for region in Region} == {
        "united_states",
        "brazil",
        "argentina",
        "european_union",
    }
    assert Region("brazil") is Region.BRAZIL


def test_commodities_serialize_as_strings() -> None:
    assert json.loads(json.dumps(list(Commodity))) == [
        "soybeans",
        "soybean_meal",
        "soybean_oil",
    ]


@pytest.mark.parametrize("enum_type", [Region, Commodity])
def test_unknown_identifier_is_rejected(enum_type: type[Region | Commodity]) -> None:
    with pytest.raises(ValueError):
        enum_type("unknown")
