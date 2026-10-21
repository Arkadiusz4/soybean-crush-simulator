# Local datasets

Downloaded datasets and derived files belong here. Everything except this README,
including `data/raw/`, is ignored by Git. Do not commit full datasets or archives.

## USDA PSD Oilseeds CSV

1. Download the official [PSD Oilseeds ZIP](https://apps.fas.usda.gov/psdonline/downloads/psd_oilseeds_csv.zip)
   manually. No automatic downloader is implemented.
2. Create `data/raw/`, extract the archive, and place the CSV at
   `data/raw/psd_oilseeds.csv` (rename the extracted file if necessary).
3. Record the source URL, retrieval date, release/version information, original
   units, selected periods, and usage conditions in a local manifest. Keep the
   original CSV unchanged.

From the repository root, the implemented loader can be used in Python:

```python
from soybean_sim.data.usda import load_usda_psd_csv
from soybean_sim.models import Commodity, Region

balances = load_usda_psd_csv(
    "data/raw/psd_oilseeds.csv",
    start_year=2020,
    end_year=2024,
    regions=[Region.BRAZIL, Region.ARGENTINA],
    commodities=[Commodity.SOYBEANS],
)
```

Run Python through `uv run --locked python`. The result is a tuple of
`MarketBalance` records sorted by region, commodity, and marketing-year string.
Use `marketing_year="2024"` instead of range bounds to select one year.

Supported countries are `US` (United States), `BR` (Brazil), `AR` (Argentina), and
`E4` (European Union). Standard commodities are `2222000` (soybeans), `0813100`
(meal), and `4232000` (oil). `(Local)` commodity variants, EU-15 (`E2`), other
countries, and individual EU member states are excluded rather than aggregated.

Supported attributes are Production, Imports, Exports, Domestic Consumption,
Beginning Stocks, Ending Stocks, and soybean Crush. Other consumption categories
are not substituted for Domestic Consumption. Meal/oil Crush rows are ignored.

Only unit `08` / `(1000 MT)` is accepted for these observations; values are
multiplied by 1,000 to produce metric tonnes. Missing attributes and blank values
remain `None`; actual zero remains zero. Identical duplicate observations are used
once, while conflicting duplicates raise an error rather than selecting a release
or summing values. Missing-value strings such as `NA` are not inferred: they fail
numeric validation. Invalid data, file, or CSV errors raise `USDADataError`.
Its subclasses distinguish file access (`USDAFileError`), CSV structure
(`USDAFormatError`), and invalid or conflicting observations (`USDAObservationError`).
All four types are available from `soybean_sim.data.usda`.

The loader preserves `Market_Year` as its four-digit string. `Calendar_Year` and
`Month` are not monthly production periods and do not affect grouping. No monthly
allocation or simulation initialization is performed by the USDA loader.

## World Bank Pink Sheet monthly prices

1. Open the official [Commodity Markets page](https://www.worldbank.org/en/research/commodity-markets)
   and download **Monthly prices** from the **Pink Sheet Data** section.
2. Keep the workbook at `data/raw/CMO-Historical-Data-Monthly.xlsx`. Record its
   retrieval date and update label in a local manifest. Keep the original unchanged.

```python
from soybean_sim.data.world_bank import load_world_bank_prices

prices = load_world_bank_prices("data/raw/CMO-Historical-Data-Monthly.xlsx")
```

The loader reads `Monthly Prices` with names in row 5, units in row 6, and month
labels in column A from row 7. It locates Soybeans, Soybean meal, and Soybean oil
by name and verifies their `($/mt)` units: nominal USD per metric tonne. Labels
such as `2026M09` become first-of-month dates. Results are `CommodityPrice` records
sorted by month and commodity, labeled `World Bank Pink Sheet`.

Optional `start_month` and `end_month` filters are inclusive first-of-month Python
dates; `commodities` selects a subset of the project's enum values. No matches or
an empty commodity selection returns `()`. All three headers and units are still
required. These are global reference prices, not regional transaction prices.

Blank prices and the verified `...` / `…` markers are skipped individually.
Unknown markers, invalid numbers, nonfinite values, and nonpositive prices fail.
Equivalent duplicates count once; conflicting known values fail. Missing duplicate
cells do not replace known values. No months, prices, or regional adjustments are
invented. File and data failures use `WorldBankDataError`, available from the same
public package; invalid filter arguments use `ValueError`.

See [the detailed loader contract](../docs/data-sources.md#implemented-local-monthly-price-loader)
for filtering, validation order, and limitations. The small fixture under
`tests/fixtures/world_bank/` allows offline tests without this raw workbook.

See [data sources](../docs/data-sources.md) for verified identifier mappings and
remaining ingestion decisions. Python ingestion code lives in
`src/soybean_sim/data/`; small synthetic test fixtures live in `tests/fixtures/usda/`.
