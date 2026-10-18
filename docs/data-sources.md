# Data sources

Historical data will initialize regional quantities and reference prices. No
datasets, downloaders, API clients, or preprocessing pipelines are implemented
or bundled yet.

## USDA Production, Supply and Distribution (PSD)

- [PSD Online](https://apps.fas.usda.gov/psdonline/)
- [USDA FAS Open Data portal](https://apps.fas.usda.gov/opendatawebv2/)

Expected inputs are soybean production, crushing, imports, exports, domestic
consumption, beginning and ending stocks, and soybean meal and oil production
and consumption. Coverage and definitions must be checked for each selected
commodity, region, and reporting period.

These quantities will inform initial inventories, production and consumption
levels, and regional supply balances. Crushing observations can inform activity
levels, but do not by themselves establish process yields or agent behavior.
Exports and API documentation will be evaluated before choosing an access method;
no endpoint, response format, or dataset schema is assumed here.

## World Bank Commodity Markets (Pink Sheet)

The [Commodity Markets page](https://www.worldbank.org/en/research/commodity-markets)
provides monthly and annual historical price downloads. The project plans to use
monthly soybean, soybean meal, and soybean oil prices as reference prices for
initialization and comparison with model behavior.

Reference series must not be treated as observed transaction prices for all four
regions. Any regional price differences or transport adjustments will require
explicit modeling assumptions. Historical prices are not a direct forecast of
future simulation prices.

## Reporting periods and units

USDA balances are generally annual or marketing-year quantities. Marketing-year
boundaries can differ across regions and commodities. World Bank monthly prices
follow calendar months. Quantities, stocks, and prices therefore cannot be joined
as if they shared a single reporting period or unit.

The initial model will allocate annual flows into monthly values using a simple,
documented rule. The exact rule and seasonality are open decisions; equal division
across twelve months is a possible baseline, not a finalized rule. Beginning and
ending stocks are boundary measurements and must not be divided like annual flows.

Before combining sources, normalize commodity and region identifiers, physical
units, currencies, price quotation bases, and reporting periods. Preserve original
units and dates in the provenance record. Historical EU membership and the chosen
initialization month also need explicit treatment.

The implemented `MarketBalance` model preserves marketing-year labels and accepts
explicit `None` for missing quantities; it does not perform annual-to-monthly
allocation. Ingestion must convert quantities to metric tons before constructing
models, including any source quantities reported in thousands of tons. Monthly
prices use `CommodityPrice` with USD per metric ton, a first-of-month date, and a
source label. Known initial stocks use `Inventory`. See the
[model contracts](architecture.md#market-input-models).

## Planned processing and validation

1. Start with downloaded exports. Record source URLs, retrieval dates, release
   versions, selected periods, units, and usage or redistribution conditions.
2. Preserve original files under root `data/`, with derived files kept separately.
   Implement ingestion code in `src/soybean_sim/data/`.
3. Map identifiers to the shared enums and check required coverage, missing values,
   duplicate observations, incompatible units, and revisions. Missing observations
   must not silently become zero.
4. Check plausible stock and flow balances using documented source definitions,
   allowing for reporting differences and rounding rather than forcing equality.
5. Apply the agreed monthly allocation and pass validated inputs to initialization.
   Future tests should use small redistributable fixtures rather than live services.

Production, use, trade, stocks, crushing activity, and reference prices are expected
historical inputs. Monthly allocation, agent rules, price adjustment, crush yields,
trade capacity, transportation costs, and scenario shocks are model assumptions
unless suitable evidence is selected. Those choices and their provenance belong
in [the assumptions document](assumptions.md).
