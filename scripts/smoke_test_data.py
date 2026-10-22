"""Smoke-test simulation input assembly using local USDA and World Bank datasets."""

from datetime import date

from soybean_sim.data import build_simulation_input
from soybean_sim.data.usda import load_usda_psd_csv
from soybean_sim.data.world_bank import load_world_bank_prices
from soybean_sim.models import Commodity, Region

balances = load_usda_psd_csv(
    "data/raw/psd_oilseeds.csv",
    marketing_year="2024",
)

prices = load_world_bank_prices(
    "data/raw/CMO-Historical-Data-Monthly.xlsx",
    start_month=date(2025, 1, 1),
    end_month=date(2025, 1, 1),
)

simulation_input = build_simulation_input(
    balances,
    prices,
    marketing_year="2024",
    price_month=date(2025, 1, 1),
    regions=list(Region),
    commodities=list(Commodity),
)

print("Simulation input created successfully!")
print(f"Balances: {len(simulation_input.balances)}")
print(f"Prices: {len(simulation_input.prices)}")
print(f"Inventories: {len(simulation_input.inventories)}")

for price in simulation_input.prices:
    print(f"{price.commodity.value}: {price.price_usd_per_tonne:.2f} USD/t")
