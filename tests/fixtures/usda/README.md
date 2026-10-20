# USDA CSV test fixture

`psd_sample.csv` uses the column names, identifiers, descriptions, and unit pairs
verified in the local USDA PSD Oilseeds export. All quantities are synthetic test
values, not historical USDA observations.

It contains the four supported regions and three standard commodities, two
marketing years, missing and zero quantities, ignored attributes with other units,
meal/oil crush rows, EU-15, unrelated countries/commodities, and `(Local)` variants.
The intentionally invalid values on excluded records ensure they are filtered
before numeric conversion. Tests construct other invalid inputs in temporary files.
