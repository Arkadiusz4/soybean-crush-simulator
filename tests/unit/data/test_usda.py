import csv
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from soybean_sim.data.usda import (
    USDADataError,
    USDAFileError,
    USDAFormatError,
    USDAObservationError,
    errors,
    load_usda_psd_csv,
    parsing,
)
from soybean_sim.data.usda.loader import load_usda_psd_csv as implementation
from soybean_sim.models import Commodity, Region


def test_public_exports_refer_to_the_implementation() -> None:
    """Check that package imports expose the original loader and exception objects."""
    assert load_usda_psd_csv is implementation
    assert USDADataError is parsing.USDADataError


@pytest.mark.parametrize(
    "error_type", [USDAFileError, USDAFormatError, USDAObservationError]
)
def test_specific_errors_remain_compatible_with_base_error(
    error_type: type[USDADataError],
) -> None:
    """Keep specific failure categories catchable through the original base error."""
    assert getattr(errors, error_type.__name__) is error_type
    assert issubclass(error_type, USDADataError)
    assert issubclass(error_type, ValueError)


@pytest.fixture
def sample_csv() -> Path:
    """Locate the PSD fixture with synthetic values and verified source codes."""
    return Path(__file__).resolve().parents[2] / "fixtures/usda/psd_sample.csv"


@pytest.fixture
def rows(sample_csv: Path) -> list[dict[str, str]]:
    """Read the PSD fixture as string cells, preserving leading zeros in identifiers."""
    with sample_csv.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


@pytest.fixture
def write_csv(
    tmp_path: Path, rows: list[dict[str, str]]
) -> Callable[[list[dict[str, str]]], Path]:
    """Provide a writer for temporary CSV exports using the fixture's column names.

    Each call replaces input.csv in the test directory, allowing tests to isolate
    invalid observations without modifying the checked-in fixture.
    """
    columns = tuple(rows[0])

    def write(observations: list[dict[str, str]]) -> Path:
        """Write the supplied observations and a header to a temporary UTF-8 CSV."""
        path = tmp_path / "input.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(observations)
        return path

    return write


def test_maps_only_standard_commodities_and_supported_regions(sample_csv: Path) -> None:
    """Check supported source mappings while excluding local variants and EU-15."""
    balances = load_usda_psd_csv(sample_csv)
    assert isinstance(balances, tuple)
    assert len(balances) == 13
    assert {balance.region for balance in balances} == set(Region)
    assert {balance.commodity for balance in balances} == set(Commodity)
    assert len({(item.region, item.commodity) for item in balances}) == 12
    eu = [item for item in balances if item.region is Region.EUROPEAN_UNION]
    assert len(eu) == 3
    assert {item.production_tonnes for item in eu} == {10000, 11000, 3000}
    meal = next(
        item
        for item in balances
        if item.region is Region.BRAZIL and item.commodity is Commodity.SOYBEAN_MEAL
    )
    assert meal.production_tonnes == 90000  # The leading-zero 0813100 code is retained.


def test_groups_attributes_and_converts_thousands_of_tonnes(sample_csv: Path) -> None:
    """Check annual attribute grouping and conversion to metric tonnes."""
    (balance,) = load_usda_psd_csv(
        str(sample_csv),
        marketing_year="2024",
        regions=[Region.BRAZIL],
        commodities=[Commodity.SOYBEANS],
    )
    assert balance.marketing_year == "2024"
    assert balance.production_tonnes == 125500
    assert balance.imports_tonnes == 0
    assert balance.exports_tonnes == 20000
    assert balance.domestic_consumption_tonnes == 90000
    assert balance.beginning_stocks_tonnes == 5000
    assert balance.ending_stocks_tonnes == 20000
    assert balance.crush_tonnes == 75000


def test_absent_and_blank_values_do_not_become_zero(sample_csv: Path) -> None:
    """Check that missing quantities stay unknown without consumption substitutes."""
    (balance,) = load_usda_psd_csv(sample_csv, marketing_year="2023")
    assert balance.production_tonnes == 120000
    assert balance.imports_tonnes is None  # Attribute absent.
    assert balance.ending_stocks_tonnes is None  # Attribute present with blank Value.
    assert balance.domestic_consumption_tonnes is None  # Food Use is not a substitute.
    assert balance.crush_tonnes is None


def test_meal_and_oil_crush_rows_are_not_loaded(sample_csv: Path) -> None:
    """Check that crush quantities are excluded from meal and oil balances."""
    balances = load_usda_psd_csv(
        sample_csv, commodities=[Commodity.SOYBEAN_MEAL, Commodity.SOYBEAN_OIL]
    )
    assert all(item.crush_tonnes is None for item in balances)


@pytest.mark.parametrize(
    ("filters", "expected_years", "count"),
    [
        ({"marketing_year": "2024"}, {"2024"}, 12),
        ({"start_year": 2023, "end_year": 2024}, {"2023", "2024"}, 13),
        ({"start_year": 2024}, {"2024"}, 12),
        ({"end_year": 2023}, {"2023"}, 1),
        ({"marketing_year": "2022"}, set(), 0),
        ({"regions": []}, set(), 0),
        ({"commodities": []}, set(), 0),
    ],
)
def test_year_and_empty_filters(
    sample_csv: Path, filters: dict[str, Any], expected_years: set[str], count: int
) -> None:
    """Check inclusive year filtering and empty selections that return no balances."""
    balances = load_usda_psd_csv(sample_csv, **filters)
    assert len(balances) == count
    assert {balance.marketing_year for balance in balances} == expected_years


@pytest.mark.parametrize(
    "filters",
    [
        {"marketing_year": "2024", "start_year": 2023},
        {"marketing_year": "2024", "end_year": 2025},
        {"marketing_year": "2024/25"},
        {"marketing_year": 2024},
        {"marketing_year": "0000"},
        {"start_year": 2025, "end_year": 2023},
        {"start_year": True},
        {"start_year": 2024.0},
        {"end_year": 10000},
        {"end_year": 0},
        {"regions": ["unknown"]},
        {"commodities": ["unknown"]},
    ],
)
def test_invalid_filters_are_rejected(
    sample_csv: Path, filters: dict[str, Any]
) -> None:
    """Check rejection of conflicting years, invalid bounds, and unknown identifiers."""
    with pytest.raises(ValueError) as caught:
        load_usda_psd_csv(sample_csv, **filters)
    assert not isinstance(caught.value, USDADataError)


@pytest.mark.parametrize("marketing_year", ["not-a-year", 2024, "0000"])
def test_conflicting_year_selectors_take_precedence_over_label_validation(
    sample_csv: Path, marketing_year: str | int
) -> None:
    """Check selector conflicts before validating the supplied year label."""
    with pytest.raises(ValueError, match="cannot be combined with year range bounds"):
        load_usda_psd_csv(sample_csv, marketing_year=marketing_year, start_year=2024)


def test_output_order_is_stable_when_rows_are_reordered(
    sample_csv: Path, rows: list[dict[str, str]], write_csv: Callable
) -> None:
    """Check that reversed CSV rows produce the same balances in sorted key order."""
    expected = load_usda_psd_csv(sample_csv)
    actual = load_usda_psd_csv(write_csv(list(reversed(rows))))
    assert actual == expected
    keys = [
        (item.region.value, item.commodity.value, item.marketing_year)
        for item in actual
    ]
    assert keys == sorted(keys)


@pytest.mark.parametrize("value", ["125.50000", "1.255e2"])
def test_identical_duplicates_do_not_sum(
    rows: list[dict[str, str]], write_csv: Callable, value: str
) -> None:
    """Check that equivalent duplicates count once regardless of reporting date."""
    duplicate = {**rows[0], "Value": value, "Calendar_Year": "2025", "Month": "12"}
    (balance,) = load_usda_psd_csv(write_csv([rows[0], duplicate]))
    assert balance.production_tonnes == 125500
    assert balance.marketing_year == "2024"


def test_duplicate_missing_values_are_accepted(
    rows: list[dict[str, str]], write_csv: Callable
) -> None:
    """Check that repeated blank observations remain one unknown quantity."""
    row = {**rows[0], "Value": ""}
    (balance,) = load_usda_psd_csv(write_csv([row, row]))
    assert balance.production_tonnes is None


@pytest.mark.parametrize("value", ["126", "", "125.5000000000000000000000000000000001"])
def test_conflicting_duplicates_report_key_and_line(
    rows: list[dict[str, str]], write_csv: Callable, value: str
) -> None:
    """Check that duplicate conflicts report the file, row, and annual attribute."""
    path = write_csv([rows[0], {**rows[0], "Value": value}])
    with pytest.raises(USDAObservationError) as caught:
        load_usda_psd_csv(path)
    message = str(caught.value)
    assert str(path) in message
    assert "row 3" in message
    assert "conflicting duplicate" in message
    assert "brazil, soybeans, 2024, production_tonnes" in message


@pytest.mark.parametrize(
    "value",
    [
        "garbage",
        "1,000",
        "1_000",
        "NA",
        "NaN",
        "inf",
        "-1",
        "1e9999",
        "1e-9999",
        "1e9999999",
    ],
)
def test_invalid_values_report_the_csv_line(
    rows: list[dict[str, str]], write_csv: Callable, value: str
) -> None:
    """Check file and row context for invalid or unrepresentable quantities."""
    path = write_csv([{**rows[0], "Value": value}])
    with pytest.raises(USDAObservationError) as caught:
        load_usda_psd_csv(path)
    assert "Value" in str(caught.value)
    assert "row 2" in str(caught.value)
    assert str(path) in str(caught.value)


@pytest.mark.parametrize(("value", "tonnes"), [("0.000125", 0.125), ("1e-325", 1e-322)])
def test_unit_conversion_precedes_float_representation(
    rows: list[dict[str, str]], write_csv: Callable, value: str, tonnes: float
) -> None:
    """Check that decimal scaling preserves tiny values before float conversion."""
    (balance,) = load_usda_psd_csv(write_csv([{**rows[0], "Value": value}]))
    assert balance.production_tonnes == tonnes


@pytest.mark.parametrize(
    ("unit_id", "unit_description"),
    [
        ("04", "(1000 MT)"),
        ("08", "(1000 HA)"),
        ("23", "(PERCENT)"),
        ("08", "(MT)"),
        ("", ""),
    ],
)
def test_units_must_match_both_code_and_description(
    rows: list[dict[str, str]], write_csv: Callable, unit_id: str, unit_description: str
) -> None:
    """Check that both the unit code and its description must match."""
    path = write_csv(
        [{**rows[0], "Unit_ID": unit_id, "Unit_Description": unit_description}]
    )
    with pytest.raises(USDAObservationError, match="unsupported or inconsistent unit"):
        load_usda_psd_csv(path)


@pytest.mark.parametrize(
    "column", ["Commodity_Description", "Country_Name", "Attribute_Description"]
)
def test_identifier_description_mismatches_are_rejected(
    rows: list[dict[str, str]], write_csv: Callable, column: str
) -> None:
    """Check that supported identifiers must match their verified USDA descriptions."""
    path = write_csv([{**rows[0], column: "unexpected description"}])
    with pytest.raises(USDAObservationError, match=f"inconsistent {column}"):
        load_usda_psd_csv(path)


def test_recognized_attribute_description_with_wrong_id_is_rejected(
    rows: list[dict[str, str]], write_csv: Callable
) -> None:
    """Check that a known attribute name still requires its verified code."""
    with pytest.raises(USDAObservationError, match="unsupported Attribute_ID"):
        load_usda_psd_csv(write_csv([{**rows[0], "Attribute_ID": "999"}]))


@pytest.mark.parametrize("year", ["", "2024/25", "20x4", "0000"])
def test_invalid_source_marketing_year_is_rejected(
    rows: list[dict[str, str]], write_csv: Callable, year: str
) -> None:
    """Check rejection of source years that are not nonzero four-digit labels."""
    with pytest.raises(USDAObservationError, match="invalid Market_Year"):
        load_usda_psd_csv(write_csv([{**rows[0], "Market_Year": year}]))


def test_out_of_scope_rows_are_filtered_before_numeric_validation(
    rows: list[dict[str, str]], write_csv: Callable
) -> None:
    """Check that excluded records are skipped before numeric validation."""
    path = write_csv(
        [
            rows[0],
            {**rows[0], "Market_Year": "2023", "Value": "bad value"},
            {
                **rows[0],
                "Country_Code": "US",
                "Country_Name": "United States",
                "Value": "bad value",
            },
            {
                **rows[0],
                "Commodity_Code": "0813100",
                "Commodity_Description": "Meal, Soybean",
                "Value": "bad value",
            },
        ]
    )
    (balance,) = load_usda_psd_csv(
        path,
        marketing_year="2024",
        regions=[Region.BRAZIL],
        commodities=[Commodity.SOYBEANS],
    )
    assert balance.production_tonnes == 125500


@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        (
            {"Commodity_Code": "2222001", "Market_Year": "bad", "Value": "bad"},
            None,
        ),
        (
            {"Attribute_ID": "999", "Market_Year": "2023"},
            "unsupported Attribute_ID",
        ),
        (
            {
                "Commodity_Code": "0813100",
                "Attribute_ID": "007",
                "Market_Year": "bad",
                "Value": "bad",
            },
            None,
        ),
        (
            {
                "Market_Year": "2023",
                "Commodity_Description": "wrong",
                "Unit_ID": "wrong",
                "Value": "bad",
            },
            None,
        ),
        (
            {"Market_Year": "bad", "Commodity_Description": "wrong"},
            "invalid Market_Year",
        ),
        (
            {"Commodity_Description": "wrong", "Unit_ID": "wrong", "Value": "bad"},
            "inconsistent Commodity_Description",
        ),
    ],
)
def test_filtering_and_validation_keep_their_existing_order(
    rows: list[dict[str, str]],
    write_csv: Callable,
    overrides: dict[str, str],
    expected_error: str | None,
) -> None:
    """Check exclusion order and error priority for multiply invalid rows."""
    path = write_csv([{**rows[0], **overrides}])
    if expected_error is None:
        assert load_usda_psd_csv(path, marketing_year="2024") == ()
    else:
        with pytest.raises(USDADataError, match=expected_error):
            load_usda_psd_csv(path, marketing_year="2024")


def test_whitespace_and_utf8_bom_are_supported(
    rows: list[dict[str, str]], write_csv: Callable
) -> None:
    """Check that padded cells and a UTF-8 byte-order mark are accepted."""
    path = write_csv([{key: f" {value} " for key, value in rows[0].items()}])
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert load_usda_psd_csv(path)[0].production_tonnes == 125500


@pytest.mark.parametrize("kind", ["missing", "directory", "non_utf8"])
def test_unreadable_files_have_path_context(tmp_path: Path, kind: str) -> None:
    """Check path context for missing, directory, and undecodable inputs."""
    path = tmp_path / "input.csv"
    if kind == "directory":
        path.mkdir()
    elif kind == "non_utf8":
        path.write_bytes(b"\xff")
    with pytest.raises(USDAFileError) as caught:
        load_usda_psd_csv(path)
    assert str(path) in str(caught.value)
    assert "Cannot read" in str(caught.value)


@pytest.mark.parametrize(
    "column",
    [
        "Commodity_Code",
        "Commodity_Description",
        "Country_Code",
        "Country_Name",
        "Market_Year",
        "Attribute_ID",
        "Attribute_Description",
        "Unit_ID",
        "Unit_Description",
        "Value",
    ],
)
def test_missing_required_columns_are_rejected(
    tmp_path: Path, rows: list[dict[str, str]], column: str
) -> None:
    """Check that every required USDA column is named when absent from the header."""
    path = tmp_path / "missing-column.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[key for key in rows[0] if key != column],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerow(rows[0])
    with pytest.raises(USDAFormatError, match=f"missing required columns: {column}"):
        load_usda_psd_csv(path)


@pytest.mark.parametrize("extra_column", ["Value", ""])
def test_duplicate_or_empty_column_names_are_rejected(
    rows: list[dict[str, str]], write_csv: Callable, extra_column: str
) -> None:
    """Check that duplicate and empty header names fail even when no data rows exist."""
    path = write_csv([])
    path.write_text(path.read_text().strip() + f",{extra_column}\n", encoding="utf-8")
    with pytest.raises(USDAFormatError, match="duplicate or empty column names"):
        load_usda_psd_csv(path)


@pytest.mark.parametrize("line", ["one,two\n", "," * 12 + "\n", '"unterminated\n'])
def test_malformed_csv_rows_are_rejected(write_csv: Callable, line: str) -> None:
    """Check rejection of wrong row widths and unterminated quoted CSV fields."""
    path = write_csv([])
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line)
    with pytest.raises(USDAFormatError, match=r"row length|malformed CSV"):
        load_usda_psd_csv(path)


def test_empty_file_is_not_a_valid_export(tmp_path: Path) -> None:
    """Check that a file without a header is reported as missing required columns."""
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(USDAFormatError, match="missing required columns"):
        load_usda_psd_csv(path)


def test_header_only_export_returns_no_balances(write_csv: Callable) -> None:
    """Check that a valid header without observations produces an empty result."""
    assert load_usda_psd_csv(write_csv([])) == ()
