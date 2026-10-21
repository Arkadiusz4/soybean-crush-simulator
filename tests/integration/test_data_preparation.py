from datetime import date
from pathlib import Path

from soybean_sim.data import build_simulation_input
from soybean_sim.data.usda import load_usda_psd_csv
from soybean_sim.data.world_bank import load_world_bank_prices
from soybean_sim.models import Commodity, Inventory, Region


def test_assemble_from_local_loader_fixtures() -> None:
    """Combine local loader fixtures without adjusting their reporting periods."""
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    balances = load_usda_psd_csv(fixtures / "usda/psd_sample.csv")
    prices = load_world_bank_prices(fixtures / "world_bank/pink_sheet_sample.xlsx")
    initial = build_simulation_input(
        balances,
        prices,
        marketing_year="2024",
        price_month=date(2026, 7, 1),
        regions=[Region.BRAZIL],
        commodities=[Commodity.SOYBEANS],
    )
    assert len(initial.balances) == len(initial.prices) == len(initial.inventories) == 1
    assert initial.balances[0].marketing_year == "2024"
    assert initial.balances[0].production_tonnes == 125500
    assert initial.prices[0].month == date(2026, 7, 1)
    assert initial.prices[0].price_usd_per_tonne == 478
    assert initial.prices[0].data_source == "World Bank Pink Sheet"
    assert initial.inventories == (
        Inventory(
            region=Region.BRAZIL,
            commodity=Commodity.SOYBEANS,
            quantity_tonnes=5000,
        ),
    )
