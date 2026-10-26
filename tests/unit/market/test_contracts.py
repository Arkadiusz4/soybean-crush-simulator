from datetime import date, datetime

import pytest
from pydantic import BaseModel, ValidationError

from soybean_sim import market
from soybean_sim.market import (
    MarketFlow,
    MarketFlowType,
    MarketPressure,
    RegionalPrice,
    TradeExecution,
    contracts,
)
from soybean_sim.models import Commodity, Region


@pytest.fixture
def records() -> dict[type[BaseModel], dict[str, object]]:
    """Provide independent synthetic monthly contract payloads with enum strings."""
    regional = {
        "month": date(2025, 1, 1),
        "region": "brazil",
        "commodity": "soybeans",
    }
    return {
        MarketFlow: {**regional, "flow_type": "supply", "quantity_tonnes": 100},
        RegionalPrice: {**regional, "price_usd_per_tonne": 400},
        MarketPressure: {**regional, "supply_tonnes": 100, "demand_tonnes": 80},
        TradeExecution: {
            "month": date(2025, 1, 1),
            "seller_agent_id": "seller-br",
            "buyer_agent_id": "buyer-ar",
            "source_region": "brazil",
            "destination_region": "argentina",
            "commodity": "soybeans",
            "quantity_tonnes": 25,
        },
    }


@pytest.mark.parametrize(
    "model_type", [MarketFlow, RegionalPrice, MarketPressure, TradeExecution]
)
def test_valid_contracts_are_frozen_and_serializable(
    records: dict[type[BaseModel], dict[str, object]], model_type: type[BaseModel]
) -> None:
    """Accept known enum identifiers, preserve values, and reject mutation or extras."""
    model = model_type.model_validate(records[model_type])
    assert model.model_dump(mode="json")["commodity"] == Commodity.SOYBEANS.value
    assert model_type.model_validate_json(model.model_dump_json()) == model
    field = next(iter(records[model_type]))
    with pytest.raises(ValidationError, match="frozen_instance"):
        setattr(model, field, getattr(model, field))
    with pytest.raises(ValidationError, match="extra_forbidden"):
        model_type.model_validate({**records[model_type], "unexpected": 1})


@pytest.mark.parametrize(
    "model_type", [MarketFlow, RegionalPrice, MarketPressure, TradeExecution]
)
@pytest.mark.parametrize(
    "month",
    [
        date(2025, 1, 2),
        date(2025, 2, 28),
        datetime(2025, 1, 1),
        "2025-01-01",
        "2025-02-30",
        0,
        None,
    ],
)
def test_month_requires_an_explicit_first_day_date(
    records: dict[type[BaseModel], dict[str, object]],
    model_type: type[BaseModel],
    month: object,
) -> None:
    """Reject non-month boundaries, invalid dates, and implicit date coercion."""
    with pytest.raises(ValidationError, match="month"):
        model_type.model_validate({**records[model_type], "month": month})


@pytest.mark.parametrize(
    ("model_type", "field"),
    [
        (MarketFlow, "quantity_tonnes"),
        (MarketPressure, "supply_tonnes"),
        (MarketPressure, "demand_tonnes"),
        (RegionalPrice, "price_usd_per_tonne"),
        (TradeExecution, "quantity_tonnes"),
    ],
)
@pytest.mark.parametrize(
    "value", [-1, float("nan"), float("inf"), float("-inf"), True, "100", None]
)
def test_quantities_and_prices_are_strict_and_finite(
    records: dict[type[BaseModel], dict[str, object]],
    model_type: type[BaseModel],
    field: str,
    value: object,
) -> None:
    """Reject negative, nonfinite, missing, and coerced numeric inputs."""
    with pytest.raises(ValidationError, match=field):
        model_type.model_validate({**records[model_type], field: value})


@pytest.mark.parametrize(
    "flow_type", [MarketFlowType.SUPPLY, MarketFlowType.CONSUMPTION]
)
def test_flow_quantities_allow_zero_and_require_explicit_type(
    records: dict[type[BaseModel], dict[str, object]], flow_type: MarketFlowType
) -> None:
    """Keep supply and consumption explicit and unsigned, including zero flows."""
    flow = MarketFlow.model_validate(
        {**records[MarketFlow], "flow_type": flow_type, "quantity_tonnes": 0}
    )
    assert flow.flow_type is flow_type
    assert flow.quantity_tonnes == 0


def test_pressure_allows_zero_supply_and_demand(
    records: dict[type[BaseModel], dict[str, object]],
) -> None:
    """Accept an inactive regional market without inventing a price signal."""
    pressure = MarketPressure.model_validate(
        {**records[MarketPressure], "supply_tonnes": 0, "demand_tonnes": 0}
    )
    assert pressure.region is Region.BRAZIL
    assert pressure.commodity is Commodity.SOYBEANS
    assert pressure.supply_tonnes == pressure.demand_tonnes == 0


@pytest.mark.parametrize(
    ("model_type", "field"),
    [(RegionalPrice, "price_usd_per_tonne"), (TradeExecution, "quantity_tonnes")],
)
def test_prices_and_executed_trades_cannot_be_zero(
    records: dict[type[BaseModel], dict[str, object]],
    model_type: type[BaseModel],
    field: str,
) -> None:
    """Require positive regional prices and actual positive matched quantities."""
    with pytest.raises(ValidationError, match=field):
        model_type.model_validate({**records[model_type], field: 0})


@pytest.mark.parametrize(
    ("model_type", "field"),
    [
        (MarketFlow, "region"),
        (MarketFlow, "commodity"),
        (MarketFlow, "flow_type"),
        (RegionalPrice, "region"),
        (RegionalPrice, "commodity"),
        (MarketPressure, "region"),
        (MarketPressure, "commodity"),
        (TradeExecution, "source_region"),
        (TradeExecution, "destination_region"),
        (TradeExecution, "commodity"),
    ],
)
def test_unknown_enum_values_are_rejected(
    records: dict[type[BaseModel], dict[str, object]],
    model_type: type[BaseModel],
    field: str,
) -> None:
    """Reject unsupported regions, commodities, and flow directions."""
    with pytest.raises(ValidationError, match=field):
        model_type.model_validate({**records[model_type], field: "unknown"})


@pytest.mark.parametrize("field", ["seller_agent_id", "buyer_agent_id"])
@pytest.mark.parametrize("value", ["", " \t\n", 123, None])
def test_trade_ids_require_nonblank_strings(
    records: dict[type[BaseModel], dict[str, object]], field: str, value: object
) -> None:
    """Require both participants to have explicit, nonblank string identifiers."""
    with pytest.raises(ValidationError, match=field):
        TradeExecution.model_validate({**records[TradeExecution], field: value})


def test_trade_ids_are_stripped(
    records: dict[type[BaseModel], dict[str, object]],
) -> None:
    """Normalize surrounding whitespace without changing the participant IDs."""
    trade = TradeExecution.model_validate(
        {
            **records[TradeExecution],
            "seller_agent_id": " seller-br\t",
            "buyer_agent_id": "\nbuyer-ar ",
        }
    )
    assert trade.seller_agent_id == "seller-br"
    assert trade.buyer_agent_id == "buyer-ar"
    assert trade.source_region is Region.BRAZIL
    assert trade.destination_region is Region.ARGENTINA


@pytest.mark.parametrize("buyer_id", ["seller-br", " seller-br "])
def test_trade_participants_differ_after_stripping(
    records: dict[type[BaseModel], dict[str, object]], buyer_id: str
) -> None:
    """Reject self-trades even when the supplied IDs have different whitespace."""
    with pytest.raises(ValidationError, match="seller_agent_id and buyer_agent_id"):
        TradeExecution.model_validate(
            {**records[TradeExecution], "buyer_agent_id": buyer_id}
        )


def test_trade_regions_must_differ(
    records: dict[type[BaseModel], dict[str, object]],
) -> None:
    """Require physical movement between regions rather than a domestic trade."""
    with pytest.raises(ValidationError, match="source_region and destination_region"):
        TradeExecution.model_validate(
            {**records[TradeExecution], "destination_region": Region.BRAZIL}
        )


@pytest.mark.parametrize(
    "model_type", [MarketFlow, RegionalPrice, MarketPressure, TradeExecution]
)
def test_contract_fields_are_required(
    records: dict[type[BaseModel], dict[str, object]], model_type: type[BaseModel]
) -> None:
    """Keep every field explicit rather than defaulting missing observations."""
    for field in records[model_type]:
        payload = {
            key: value for key, value in records[model_type].items() if key != field
        }
        with pytest.raises(ValidationError) as caught:
            model_type.model_validate(payload)
        assert any(
            error["loc"] == (field,) and error["type"] == "missing"
            for error in caught.value.errors()
        )


@pytest.mark.parametrize(
    "name",
    [
        "MarketFlowType",
        "MarketFlow",
        "RegionalPrice",
        "MarketPressure",
        "TradeExecution",
    ],
)
def test_market_package_exports_contracts(name: str) -> None:
    """Expose stable contract classes alongside the existing market APIs."""
    assert getattr(market, name) is getattr(contracts, name)
    assert name in market.__all__
