# Local datasets

Place downloaded source datasets and derived files in this directory. Its contents
are ignored by Git except for this README. Python ingestion code will live in
`src/soybean_sim/data/`; no loaders or downloads are implemented yet.

For each future dataset, record its source URL, retrieval date, source version,
license or redistribution conditions, units, time frequency, and transformations
in a local manifest. Do not commit large datasets, credentials, or downloaded files.
See [data sources](../docs/data-sources.md) for the planned ingestion process.
