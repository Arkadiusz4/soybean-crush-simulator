from importlib import import_module

import pytest


@pytest.mark.parametrize(
    ("name", "module"),
    [
        ("Region", "enums"),
        ("Commodity", "enums"),
        ("MarketBalance", "market"),
        ("CommodityPrice", "market"),
        ("Inventory", "market"),
        ("NonNegativeTonnes", "market"),
        ("NonEmptyString", "market"),
        ("SimulationInput", "simulation"),
    ],
)
def test_public_imports_remain_compatible(name: str, module: str) -> None:
    public = import_module("soybean_sim.models")
    implementation = import_module(f"soybean_sim.models.{module}")
    assert getattr(public, name) is getattr(implementation, name)
