# Local datasets

Raw datasets and derived files are ignored by Git. Do not commit full datasets or
archives. Synthetic dashboard runs and automated fixtures do not need these files.

## Historical inputs

1. Download the [USDA PSD Oilseeds ZIP](https://apps.fas.usda.gov/psdonline/downloads/psd_oilseeds_csv.zip),
   extract it, and place the CSV at `data/raw/psd_oilseeds.csv`.
2. Download **Monthly prices** from the Pink Sheet Data section of the
   [World Bank Commodity Markets page](https://www.worldbank.org/en/research/commodity-markets)
   and place the workbook at `data/raw/CMO-Historical-Data-Monthly.xlsx`.
3. Keep the originals unchanged. Record source URLs, retrieval dates, release
   versions, units, selected periods, and usage or redistribution conditions in
   a local manifest.

The application does not download datasets. Historical years are available only
when their selected source observations and calibration are complete. See
[data sources](../docs/data-sources.md) for loader mappings, units, validation,
missing values, and Python usage. Small offline fixtures are in `tests/fixtures/`.
