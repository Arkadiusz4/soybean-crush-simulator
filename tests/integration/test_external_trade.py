from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

from soybean_sim.agents import AgentIdentity, AgentType, Crusher, CrusherConfig
from soybean_sim.market import (
    ExternalMarket,
    ExternalTradeError,
    ExternalTradeFlow,
    FlowSettlementError,
    MarketFlow,
    MarketFlowType,
    MarketState,
    PriceDynamicsConfig,
    validate_external_trades,
)
from soybean_sim.models import (
    Commodity,
    CommodityPrice,
    Inventory,
    MarketSnapshot,
    Region,
)
from soybean_sim.scenarios import (
    ArgentinaDroughtConfig,
    HistoricalMarketError,
    compare_historical_market,
    load_historical_market,
    preflight_historical_market,
    run_historical_market,
)
from soybean_sim.simulation import SimulationError, run_simulation


def test_imports_feed_crushing_and_exports_follow_consumption(
    market_smoke: ModuleType,
) -> None:
    """Conserve mass across boundary transfers, crushing, and consumption."""
    month = date(2025, 1, 1)
    state = MarketState.from_snapshot(
        MarketSnapshot(
            month=month,
            prices=tuple(
                CommodityPrice(
                    commodity=commodity,
                    month=month,
                    price_usd_per_tonne=value,
                    data_source="synthetic",
                )
                for commodity, value in (
                    (Commodity.SOYBEANS, 100),
                    (Commodity.SOYBEAN_MEAL, 200),
                    (Commodity.SOYBEAN_OIL, 300),
                )
            ),
            inventories=tuple(
                Inventory(
                    region=Region.ARGENTINA, commodity=commodity, quantity_tonnes=0
                )
                for commodity in Commodity
            ),
        )
    )
    config = CrusherConfig(monthly_capacity_tonnes=10, processing_cost_usd_per_tonne=0)
    crusher = Crusher(
        AgentIdentity(
            agent_id="crusher-ar", agent_type=AgentType.CRUSHER, region=Region.ARGENTINA
        ),
        config,
    )
    boundaries = (
        ExternalTradeFlow(
            month=month,
            source=ExternalMarket.CLEARING_BOUNDARY,
            destination=Region.ARGENTINA,
            commodity=Commodity.SOYBEANS,
            quantity_tonnes=10,
        ),
        ExternalTradeFlow(
            month=month,
            source=Region.ARGENTINA,
            destination=ExternalMarket.CLEARING_BOUNDARY,
            commodity=Commodity.SOYBEAN_MEAL,
            quantity_tonnes=5,
        ),
    )
    flows = (
        MarketFlow(
            month=month,
            region=Region.ARGENTINA,
            commodity=Commodity.SOYBEAN_MEAL,
            flow_type=MarketFlowType.SUPPLY,
            quantity_tonnes=999,
        ),
        MarketFlow(
            month=month,
            region=Region.ARGENTINA,
            commodity=Commodity.SOYBEAN_MEAL,
            flow_type=MarketFlowType.CONSUMPTION,
            quantity_tonnes=2,
        ),
    )
    options = dict(
        crusher_configs={"crusher-ar": config},
        months=1,
        monthly_flows=flows,
        external_trade_flows=boundaries,
        price_dynamics_config=PriceDynamicsConfig(),
    )
    before = state.model_dump_json()
    result = run_simulation(state, (crusher,), **options)
    assert result == run_simulation(state, (crusher,), **options)
    assert state.model_dump_json() == before
    final = {
        item.commodity: item.quantity_tonnes
        for item in result.final_state.snapshot.inventories
    }
    assert final == {
        Commodity.SOYBEANS: 0,
        Commodity.SOYBEAN_MEAL: 1,
        Commodity.SOYBEAN_OIL: 2,
    }
    assert sum(final.values()) + (5 - 10) + 2 == 0  # Regional + external + consumption.
    monthly = result.monthly_results[0]
    assert monthly.external_trade_flows == validate_external_trades(boundaries)
    assert monthly.settlement_results[0].executed_tonnes == 10
    pressures = {item.commodity: item for item in monthly.market_pressures}
    assert pressures[Commodity.SOYBEANS].supply_tonnes == 10
    assert pressures[Commodity.SOYBEAN_MEAL].supply_tonnes == 8
    assert pressures[Commodity.SOYBEAN_MEAL].demand_tonnes == 7
    market_smoke.check_accounting(result, {"crusher-ar": config})
    with pytest.raises(FlowSettlementError):
        run_simulation(state, (), **options)
    with pytest.raises(ExternalTradeError, match="export"):
        run_simulation(
            state,
            (crusher,),
            **{
                **options,
                "external_trade_flows": (
                    boundaries[0],
                    boundaries[1].model_copy(update={"quantity_tonnes": 7}),
                ),
            },
        )


def test_boundary_mode_does_not_duplicate_agent_matching(
    market_smoke: ModuleType,
) -> None:
    """Preserve ordinary matched trades while making boundary schedules exclusive."""
    case = market_smoke.create_market()
    options = dict(
        crusher_configs=case.crusher_configs,
        months=3,
        monthly_flows=case.flows,
        regional_prices=case.regional_prices,
        price_dynamics_config=case.price_config,
    )
    ordinary = run_simulation(case.state, case.agents, **options)
    boundary = run_simulation(
        case.state, case.agents, **options, external_trade_flows=()
    )
    assert any(item.executed_trades for item in ordinary.monthly_results)
    assert all(not item.executed_trades for item in boundary.monthly_results)
    assert all(item.decisions for item in boundary.monthly_results)
    market_smoke.check_accounting(boundary, case.crusher_configs)
    with pytest.raises(SimulationError, match="physical mode"):
        run_simulation(
            case.state, (), crusher_configs={}, months=1, external_trade_flows=()
        )


def test_historical_boundary_allocation_and_paired_accounting(
    historical_paths: tuple[Path, Path],
    historical_script: ModuleType,
) -> None:
    """Keep historical trade as reference data in reproducible calibration runs."""
    csv_path = historical_paths[0]
    csv_path.write_text(
        csv_path.read_text()
        .replace("057,Imports,08,(1000 MT),0", "057,Imports,08,(1000 MT),0.012")
        .replace("088,Exports,08,(1000 MT),0", "088,Exports,08,(1000 MT),0.006")
    )
    prepared = load_historical_market(*historical_paths, include_historical_trade=True)
    assert len(prepared.external_trade_flows) == 288
    assert all(
        item.quantity_tonnes == (1 if item.is_import else 0.5)
        for item in prepared.external_trade_flows
    )
    assert all(
        item.imports_tonnes == 12 and item.exports_tonnes == 6
        for item in prepared.balances
    )
    before = prepared.model_dump_json()
    crushers, traders, pricing = historical_script.create_settings(prepared)
    settings = dict(
        crusher_configs=crushers, trader_configs=traders, price_dynamics_config=pricing
    )
    assert preflight_historical_market(prepared, **settings).can_run
    result = run_historical_market(prepared, **settings)
    comparison = compare_historical_market(
        prepared, **settings, drought_config=ArgentinaDroughtConfig()
    )
    assert len(result.monthly_results) == 12
    assert comparison.baseline == result
    for history in (comparison.baseline, comparison.drought):
        assert all(not month.external_trade_flows for month in history.monthly_results)
        assert all(
            month.consumption_results is not None for month in history.monthly_results
        )
        historical_script.check_accounting(
            history,
            {f"crusher-{region.value}": config for region, config in crushers.items()},
        )
    market = comparison.monthly_metrics()[0].markets[0]
    assert market.imports_tonnes.baseline == 0
    assert market.exports_tonnes.baseline == 0
    assert prepared.model_dump_json() == before
    changed = prepared.model_copy(
        update={
            "external_trade_flows": (
                prepared.external_trade_flows[0].model_copy(
                    update={"quantity_tonnes": 999}
                ),
                *prepared.external_trade_flows[1:],
            )
        }
    )
    report = preflight_historical_market(changed, **settings)
    assert not report.can_run and "annual allocation" in " ".join(
        report.blocking_errors
    )


def test_missing_trade_is_not_zero(historical_paths: tuple[Path, Path]) -> None:
    """Reject missing annual trade when the physical schedule is requested."""
    csv_path = historical_paths[0]
    csv_path.write_text(
        csv_path.read_text().replace(
            "057,Imports,08,(1000 MT),0", "057,Imports,08,(1000 MT),"
        )
    )
    with pytest.raises(HistoricalMarketError, match="missing imports_tonnes"):
        load_historical_market(*historical_paths, include_historical_trade=True)
