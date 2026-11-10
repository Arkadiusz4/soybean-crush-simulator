from importlib import import_module

import pytest


@pytest.mark.parametrize(
    ("name", "module"),
    [
        ("Region", "_enums"),
        ("Commodity", "_enums"),
        ("MarketBalance", "_market"),
        ("CommodityPrice", "_market"),
        ("Inventory", "_market"),
        ("NonNegativeTonnes", "_market"),
        ("NonEmptyString", "_market"),
        ("SimulationInput", "_simulation"),
        ("MarketSnapshot", "_snapshot"),
    ],
)
def test_public_imports_remain_compatible(name: str, module: str) -> None:
    """Check that public model exports are identical to their implementation objects."""
    public = import_module("soybean_sim.models")
    implementation = import_module(f"soybean_sim.models.{module}")
    assert getattr(public, name) is getattr(implementation, name)
