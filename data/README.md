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

The loader preserves `Market_Year` as its four-digit string. `Calendar_Year` and
`Month` are not monthly production periods and do not affect grouping. No monthly
allocation, price loading, or simulation initialization is implemented here.

See [data sources](../docs/data-sources.md) for verified identifier mappings and
remaining ingestion decisions. Python ingestion code lives in
`src/soybean_sim/data/`; small synthetic test fixtures live in `tests/fixtures/usda/`.
