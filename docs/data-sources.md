# Data sources

Historical data will initialize regional quantities and reference prices. A local
USDA PSD CSV loader and World Bank monthly XLSX price loader are implemented.
Automatic downloads, API clients, and monthly allocation are not. Full datasets
are not bundled.

## USDA Production, Supply and Distribution (PSD)

- [PSD Online](https://apps.fas.usda.gov/psdonline/)
- [USDA FAS Open Data portal](https://apps.fas.usda.gov/opendatawebv2/)
- [Official PSD Oilseeds CSV archive](https://apps.fas.usda.gov/psdonline/downloads/psd_oilseeds_csv.zip)

Expected inputs are soybean production, crushing, imports, exports, domestic
consumption, beginning and ending stocks, and soybean meal and oil production
and consumption. Coverage and definitions must be checked for each selected
commodity, region, and reporting period.

These quantities will inform initial inventories, production and consumption
levels, and regional supply balances. Crushing observations can inform activity
levels, but do not by themselves establish process yields or agent behavior.
The loader uses the published CSV export. API access is outside the current scope.

### Implemented local CSV loader

Download the archive manually, extract it, and place the CSV at
`data/raw/psd_oilseeds.csv`. See [local dataset instructions](../data/README.md).
The entire `data/raw/` directory is ignored by Git.

The mappings below were verified against the local export's codes and descriptions.
Identifiers are read as strings, preserving leading zeros.

| Commodity code | USDA description | Model commodity |
| --- | --- | --- |
| `2222000` | Oilseed, Soybean | `soybeans` |
| `0813100` | Meal, Soybean | `soybean_meal` |
| `4232000` | Oil, Soybean | `soybean_oil` |

| Country code | USDA name | Model region |
| --- | --- | --- |
| `US` | United States | `united_states` |
| `BR` | Brazil | `brazil` |
| `AR` | Argentina | `argentina` |
| `E4` | European Union | `european_union` |

| Attribute ID | USDA description | Model field |
| --- | --- | --- |
| `028` | Production | `production_tonnes` |
| `057` | Imports | `imports_tonnes` |
| `088` | Exports | `exports_tonnes` |
| `125` | Domestic Consumption | `domestic_consumption_tonnes` |
| `020` | Beginning Stocks | `beginning_stocks_tonnes` |
| `176` | Ending Stocks | `ending_stocks_tonnes` |
| `007` | Crush | `crush_tonnes` (soybeans only) |

Only standard commodities are loaded. Separate `(Local)` codes `2222001`,
`0813101`, and `4232001` are excluded; they are never merged into standard balances.
`E2` / EU-15 and individual EU member states are excluded. The loader uses `E4`
directly, without constructing an EU aggregate or backfilling its historical gaps.
Crush appears on meal and oil source records too; these rows are ignored because
the existing model defines crush only as soybean input volume. Area, yield, rates,
total supply, and alternative domestic consumption categories are also ignored.

Selected observations require the exact unit pair `08` / `(1000 MT)` and are
multiplied by 1,000. Other units, mismatched descriptions, nonfinite or negative
quantities, and malformed numbers raise `USDADataError` with file/row context.
Values outside the model's finite float range are rejected. Missing attributes or
blank `Value` cells become `None`, never zero. Other missing-value tokens such as
`NA` are not guessed. Required columns are the ten used for identifiers,
descriptions, units, year, and value; extra columns are allowed. `Calendar_Year`
and `Month` are optional and ignored, never used as monthly production dates.

Duplicate keys are region, commodity, marketing year, and attribute. Numerically
identical source values (including equivalent decimal representations) are used
once. Different values, including missing versus known values, are conflicts.
No summation or automatic selection of the latest release is performed.

```python
from soybean_sim.data.usda import load_usda_psd_csv
from soybean_sim.models import Commodity, Region

balances = load_usda_psd_csv(
    "data/raw/psd_oilseeds.csv",
    marketing_year="2024",
    regions=[Region.UNITED_STATES, Region.BRAZIL],
    commodities=[Commodity.SOYBEANS, Commodity.SOYBEAN_MEAL],
)
```

The public API returns `tuple[MarketBalance, ...]`, sorted by region identifier,
commodity identifier, then marketing year. Use inclusive `start_year=2020,
end_year=2024` bounds instead of `marketing_year` for a range; either bound can be
omitted. Omitted region/commodity filters select all supported values, while empty
collections select none. No matches return an empty tuple. Invalid filters raise
`ValueError`; file and CSV problems raise `USDADataError`, a `ValueError` subclass.

For selective handling, import these subclasses from `soybean_sim.data.usda`:

- `USDAFileError`: the file cannot be opened, read, or decoded as UTF-8.
- `USDAFormatError`: malformed CSV syntax, missing/invalid headers, or wrong row widths.
- `USDAObservationError`: invalid quantities, units, years, descriptions, or duplicates
  with conflicting values.

All inherit from `USDADataError`, so existing catch blocks continue to work.

`Market_Year` remains the original four-digit string. Loading several years does
not construct `SimulationInput`, infer monthly flows, reconcile accounting
equations, or establish a policy for historical EU membership or revisions.

## World Bank Commodity Markets (Pink Sheet)

The [Commodity Markets page](https://www.worldbank.org/en/research/commodity-markets)
provides monthly and annual historical price downloads. The project plans to use
monthly soybean, soybean meal, and soybean oil prices as reference prices for
initialization and comparison with model behavior.

### Implemented local monthly price loader

On the source page, choose **Monthly prices** under **Pink Sheet Data**, download
the historical Excel workbook manually, and place it at
`data/raw/CMO-Historical-Data-Monthly.xlsx`. Record the retrieval date and release
label with the local dataset. The loader never downloads, saves, or modifies it.

The local workbook inspected for this implementation is labeled
`Updated on October 02, 2026`. Its `Monthly Prices` worksheet has commodity names
in row 5, units in row 6, and observations from row 7. Column A contains calendar
months such as `1960M01` and `2026M09`. The three series were found in Y–AA;
the loader discovers them by header name so their columns may move.

| Source header | Model commodity | Verified source unit |
| --- | --- | --- |
| Soybeans | `soybeans` | `($/mt)` |
| Soybean meal | `soybean_meal` | `($/mt)` |
| Soybean oil | `soybean_oil` | `($/mt)` |

`($/mt)` means nominal USD per metric tonne, matching `CommodityPrice` directly.
All three headers must be present exactly once outside column A and all three
units must match, even when selecting fewer commodities. Surrounding whitespace
is trimmed. Other worksheets and commodity series are not used as price inputs.
Missing or changed layouts are errors rather than a reason to guess new mappings.

```python
from datetime import date

from soybean_sim.data.world_bank import load_world_bank_prices
from soybean_sim.models import Commodity

prices = load_world_bank_prices(
    "data/raw/CMO-Historical-Data-Monthly.xlsx",
    start_month=date(2020, 1, 1),
    end_month=date(2024, 12, 1),
    commodities=[Commodity.SOYBEANS, Commodity.SOYBEAN_MEAL],
)
```

The return type is `tuple[CommodityPrice, ...]`, sorted by calendar month and
commodity identifier. Dates are the first day of each month. Bounds must be
first-of-month `date` objects, not strings or datetimes, and are inclusive. None
selects all supported commodities; an empty selection or no matches returns `()`.
A valid header-only worksheet also returns `()`. Missing months are not generated.
Each record has `data_source="World Bank Pink Sheet"`.

Blank cells, whitespace-only strings, `...`, and `…` skip only the affected price.
Other valid prices in that month remain. Unknown markers such as `NA` and `N/A`
are errors. Known values must be positive and finite and fit the model's float
representation. Numeric strings are accepted; booleans, dates, formulas, Excel
errors, zero, and negative prices are rejected. No rounding, inflation or currency
adjustment, conversion, interpolation, or forward-filling is applied.

Duplicate keys are month and commodity. Equivalent decimal values count once;
conflicting known values fail, even if they would convert to the same float.
Missing duplicate cells are skipped and do not overwrite known prices.

Blank worksheet rows are ignored. All other month labels must be valid before
range filtering. Price values are validated only for selected months and
commodities, after the complete header and unit checks. Invalid arguments raise
`ValueError`; file, structure, and observation failures raise `WorldBankDataError`,
also a `ValueError` subclass. Import it from `soybean_sim.data.world_bank`.
Messages include the path and available sheet, row, commodity, and month context;
underlying file or workbook errors retain their original exception as the cause.

Tests use a six-month extract under `tests/fixtures/world_bank/` and never require
the full raw workbook or network access. Integrating prices with USDA balances
and constructing simulation inputs remain future work.

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

## Processing and remaining work

1. Start with downloaded exports. Record source URLs, retrieval dates, release
   versions, selected periods, units, and usage or redistribution conditions.
2. Preserve original files under root `data/`, with derived files kept separately.
   Implement ingestion code in `src/soybean_sim/data/`.
3. The loaders map identifiers and validate missing values, duplicate observations,
   and source units. Complete coverage and revision policies remain
   to be defined. Missing observations must not silently become zero.
4. Check plausible stock and flow balances using documented source definitions,
   allowing for reporting differences and rounding rather than forcing equality.
5. Apply the agreed monthly allocation and pass validated inputs to initialization.
   Future tests should use small redistributable fixtures rather than live services.

Production, use, trade, stocks, crushing activity, and reference prices are expected
historical inputs. Monthly allocation, agent rules, price adjustment, crush yields,
trade capacity, transportation costs, and scenario shocks are model assumptions
unless suitable evidence is selected. Those choices and their provenance belong
in [the assumptions document](assumptions.md).
