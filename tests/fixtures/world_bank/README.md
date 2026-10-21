# World Bank Pink Sheet fixture

`pink_sheet_sample.xlsx` is a small extract of the local file
`data/raw/CMO-Historical-Data-Monthly.xlsx`, inspected on 2026-10-21. Its update
label is `Updated on October 02, 2026`. The source is the World Bank's
[Commodity Markets monthly prices download](https://www.worldbank.org/en/research/commodity-markets).

Source SHA-256:
`ea1c350827878ea3bbe30e3cda16a13fd3bd5b409b8458940dc94a36b5a33154`.

The fixture retains the first six rows and columns A, B, Y, Z, and AA, with six
observation rows copied unchanged. Column B is an unrelated series. The small
`Description` sheet is a test note, not a copy of the original description sheet.

| Month | Soybeans | Soybean oil | Soybean meal |
| --- | ---: | ---: | ---: |
| 1960M01 | 94 | 204 | 92 |
| 1960M02 | 91 | 201 | 87 |
| 1960M03 | 92 | 201 | 84 |
| 2026M07 | 478 | 1658 | 410 |
| 2026M08 | 482 | 1639 | 406 |
| 2026M09 | 524 | 1615 | 447 |

Units are `($/mt)`, nominal USD per metric tonne. Missing-price and malformed-data
tests modify temporary copies; those modifications are synthetic test cases.
The fixture deliberately has a gap between 1960 and 2026. No months are generated
to fill it. Automated tests require neither the full source workbook nor network
access.
