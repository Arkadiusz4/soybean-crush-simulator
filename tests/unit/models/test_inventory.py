from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from soybean_sim.models import (
    Inventory,
)


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), None, True, "5"])
def test_inventory_requires_a_known_nonnegative_quantity(
    records: dict[type[BaseModel], dict[str, Any]], value: Any
) -> None:
    """Check that stock rejects missing, negative, nonfinite, and coerced quantities."""
    records[Inventory]["quantity_tonnes"] = value
    with pytest.raises(ValidationError, match="quantity_tonnes"):
        Inventory.model_validate(records[Inventory])


def test_zero_inventory_is_valid(
    records: dict[type[BaseModel], dict[str, Any]],
) -> None:
    """Check that known zero stock is a valid inventory quantity."""
    records[Inventory]["quantity_tonnes"] = 0
    assert Inventory.model_validate(records[Inventory]).quantity_tonnes == 0
