from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from soybean_sim.models import (
    CommodityPrice,
    Inventory,
    MarketBalance,
    SimulationInput,
)


@pytest.mark.parametrize(
    ("model_type", "field"),
    [
        (MarketBalance, "region"),
        (MarketBalance, "commodity"),
        (CommodityPrice, "commodity"),
        (Inventory, "region"),
        (Inventory, "commodity"),
    ],
)
def test_unknown_identifiers_are_rejected(
    records: dict[type[BaseModel], dict[str, Any]],
    model_type: type[BaseModel],
    field: str,
) -> None:
    """Check that all regional and commodity fields reject unsupported identifiers."""
    records[model_type][field] = "unknown"
    with pytest.raises(ValidationError, match=field):
        model_type.model_validate(records[model_type])


@pytest.mark.parametrize(
    ("model_type", "field"),
    [(MarketBalance, "marketing_year"), (CommodityPrice, "data_source")],
)
@pytest.mark.parametrize("value", ["", "  ", 2021, None])
def test_reporting_label_and_source_must_be_present(
    records: dict[type[BaseModel], dict[str, Any]],
    model_type: type[BaseModel],
    field: str,
    value: Any,
) -> None:
    """Check that year labels and price sources require nonblank strings."""
    records[model_type][field] = value
    with pytest.raises(ValidationError, match=field):
        model_type.model_validate(records[model_type])


@pytest.mark.parametrize(
    ("model_type", "field"),
    [
        (MarketBalance, field)
        for field in (
            "region",
            "commodity",
            "marketing_year",
            "production_tonnes",
            "imports_tonnes",
            "exports_tonnes",
            "domestic_consumption_tonnes",
            "beginning_stocks_tonnes",
            "ending_stocks_tonnes",
        )
    ]
    + [
        (CommodityPrice, field)
        for field in (
            "commodity",
            "month",
            "price_usd_per_tonne",
            "data_source",
        )
    ]
    + [(Inventory, field) for field in ("region", "commodity", "quantity_tonnes")]
    + [(SimulationInput, field) for field in ("balances", "prices", "inventories")],
)
def test_missing_required_fields_do_not_default_to_zero(
    records: dict[type[BaseModel], dict[str, Any]],
    model_type: type[BaseModel],
    field: str,
) -> None:
    """Check that omitted required fields raise errors instead of using defaults."""
    del records[model_type][field]
    with pytest.raises(ValidationError) as caught:
        model_type.model_validate(records[model_type])
    assert any(
        error["loc"] == (field,) and error["type"] == "missing"
        for error in caught.value.errors()
    )


@pytest.mark.parametrize(
    "model_type", [MarketBalance, CommodityPrice, Inventory, SimulationInput]
)
def test_models_round_trip_and_reject_extra_fields_and_mutation(
    records: dict[type[BaseModel], dict[str, Any]], model_type: type[BaseModel]
) -> None:
    """Check JSON round trips, immutable fields, and rejection of undeclared data."""
    model = model_type.model_validate(records[model_type])
    assert model_type.model_validate_json(model.model_dump_json()) == model
    field = next(iter(records[model_type]))
    with pytest.raises(ValidationError, match="frozen"):
        setattr(model, field, getattr(model, field))
    with pytest.raises(ValidationError, match="extra_forbidden"):
        model_type.model_validate({**records[model_type], "unexpected": 1})
