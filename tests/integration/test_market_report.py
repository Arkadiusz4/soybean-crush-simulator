import re
from io import StringIO
from types import ModuleType
from unittest.mock import Mock

import pytest
from rich.console import Console


def test_default_report_is_concise_and_sections_are_ordered(
    market_smoke: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep the demonstration concise while retaining all validation outcomes.

    Args:
        market_smoke (ModuleType): Shared synthetic script and presentation helpers.
        monkeypatch (pytest.MonkeyPatch): Fixed terminal width for readable tables.
        capsys (pytest.CaptureFixture[str]): Captured standard streams.

    Returns:
        None: Confirms metadata, section order, totals, and seven completed checks.
    """
    monkeypatch.setenv("COLUMNS", "120")
    assert market_smoke.main([]) == 0
    captured = capsys.readouterr()
    output = captured.out
    titles = (
        "Soybean Market Simulator — Milestone 3",
        "End-to-End Smoke Test",
        "Monthly Activity",
        "Regional Price Changes",
        "Final Inventories",
        "Validation Summary",
        "Overall: PASS",
    )
    assert [output.index(title) for title in titles] == sorted(
        output.index(title) for title in titles
    )
    assert "Synthetic data" in output
    assert "Period: 2025-01 to 2025-03" in output
    assert "Regions (2): Brazil, European Union" in output
    assert "Agents: 3" in output
    assert "Monthly Market Pressures" not in output
    assert "200/125" not in output
    assert re.search(r"TOTAL\s+300\.00\s+3\s+125\.00", output)
    for stage in (
        "History and carryover",
        "Physical inventory accounting",
        "Executed interregional trades",
        "Market pressure calculations",
        "Dynamic regional pricing",
        "Determinism",
        "Input immutability",
    ):
        assert re.search(rf"{stage}\s+PASS", output)
    assert captured.err == ""
    assert output.count("Overall: PASS") == 1


def test_verbose_report_adds_separate_pressure_columns(
    market_smoke: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Show pressure detail as labeled quantities rather than compact ratios.

    Args:
        market_smoke (ModuleType): Shared smoke entry point.
        monkeypatch (pytest.MonkeyPatch): Fixed terminal width.
        capsys (pytest.CaptureFixture[str]): Captured report.

    Returns:
        None: Confirms supply, demand, and correctly signed net quantities.
    """
    monkeypatch.setenv("COLUMNS", "120")
    assert market_smoke.main(["--verbose"]) == 0
    output = capsys.readouterr().out
    assert "Monthly Market Pressures" in output
    assert "Supply" in output and "Demand" in output
    assert "Net supply -" in output and "demand (t)" in output
    assert re.search(
        r"2025-01\s+Brazil\s+Soybeans\s+200\.00\s+125\.00\s+\+75\.00", output
    )
    assert re.search(
        r"2025-01\s+European Union\s+Soybean meal\s+0\.00\s+20\.00\s+-20\.00", output
    )
    assert "200/125" not in output
    assert (
        output.index("Final Inventories")
        < output.index("Monthly Market Pressures")
        < output.index("Validation Summary")
    )
    assert "Overall: PASS" in output


def test_price_changes_use_first_and_last_observations(
    market_smoke: ModuleType,
) -> None:
    """Display independently expected percentage changes without altering price data.

    Args:
        market_smoke (ModuleType): Shared synthetic setup and report builders.

    Returns:
        None: Confirms positive/negative changes and observed-month comparison labels.
    """
    case = market_smoke.create_market()
    result = market_smoke.run_market(case)
    output = StringIO()
    Console(file=output, width=120, color_system=None).print(
        market_smoke.report.regional_price_table(result)
    )
    text = output.getvalue()
    assert re.search(
        r"Brazil\s+Soybean meal\s+300\.00\s+243\.00\s+-57\.00\s+-19\.00%", text
    )
    assert re.search(
        r"European Union\s+Soybean meal\s+300\.00\s+432\.00\s+\+132\.00\s+\+44\.00%",
        text,
    )
    assert re.search(
        r"European Union\s+Soybeans\s+250\.00\s+160\.00\s+-90\.00\s+-36\.00%", text
    )
    assert "First observed month (2025-01) to last observed month (2025-03)" in text
    assert "Colors indicate direction only" in " ".join(text.split())
    market_smoke.check_prices(result)
    market_smoke.check_pressures(result)
    market_smoke.check_accounting(result, case.crusher_configs)


def test_unchanged_price_direction_is_neutral(market_smoke: ModuleType) -> None:
    """Treat a single observed month as unchanged rather than a future-price forecast.

    Args:
        market_smoke (ModuleType): Shared setup and table builder.

    Returns:
        None: Confirms zero absolute/percentage changes with no red or green styling.
    """
    case = market_smoke.create_market()
    result = market_smoke.run_market(case)
    single_month = result.model_copy(
        update={"monthly_results": result.monthly_results[:1]}
    )
    output = StringIO()
    Console(
        file=output,
        width=120,
        force_terminal=True,
        color_system="standard",
        no_color=False,
    ).print(market_smoke.report.regional_price_table(single_month))
    text = output.getvalue()
    assert "+0.00%" in text
    assert "\x1b[31m" not in text and "\x1b[32m" not in text


def test_price_direction_colors_match_the_signed_change(
    market_smoke: ModuleType,
) -> None:
    """Use directional colors consistently without assigning economic meaning.

    Args:
        market_smoke (ModuleType): Shared price table and deterministic scenario.

    Returns:
        None: Confirms increases are green and decreases red in a color terminal.
    """
    result = market_smoke.run_market(market_smoke.create_market())
    output = StringIO()
    Console(
        file=output,
        width=120,
        force_terminal=True,
        color_system="standard",
        no_color=False,
    ).print(market_smoke.report.regional_price_table(result))
    assert re.search(r"\x1b\[32m\s*\+132\.00", output.getvalue())
    assert re.search(r"\x1b\[31m\s*-57\.00", output.getvalue())


@pytest.mark.parametrize(
    ("function", "stage"),
    [
        ("create_market", "Synthetic market setup"),
        ("run_market", "Simulation execution"),
        ("check_history", "History and carryover"),
        ("check_accounting", "Physical inventory accounting"),
        ("check_pressures", "Market pressure calculations"),
        ("check_prices", "Dynamic regional pricing"),
    ],
)
def test_validation_failures_name_the_stage_and_never_claim_overall_pass(
    market_smoke: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    function: str,
    stage: str,
) -> None:
    """Render assertion failures with their active stage and a nonzero result.

    Args:
        market_smoke (ModuleType): Shared synthetic entry point.
        monkeypatch (pytest.MonkeyPatch): Replace one stage with a failing check.
        capsys (pytest.CaptureFixture[str]): Captured standard streams.
        function (str): Stage implementation to replace.
        stage (str): Human-readable stage name expected in the report.

    Returns:
        None: Confirms literal error messages and an unambiguous overall failure.
    """
    monkeypatch.setattr(
        market_smoke,
        function,
        Mock(side_effect=AssertionError("Expected [value] differs")),
    )
    assert market_smoke.main([]) == 1
    captured = capsys.readouterr()
    assert stage in captured.err
    assert "Expected [value] differs" in captured.err
    assert "Overall: FAIL" in captured.err
    assert "remaining checks were not run" in captured.err
    assert "Overall: PASS" not in captured.out + captured.err
    assert captured.out == ""


@pytest.mark.parametrize("error", [RuntimeError, TypeError, ValueError])
def test_programming_errors_are_not_swallowed(
    market_smoke: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    error: type[Exception],
) -> None:
    """Allow unexpected implementation errors to retain their traceback.

    Args:
        market_smoke (ModuleType): Shared synthetic entry point.
        monkeypatch (pytest.MonkeyPatch): Inject an unexpected setup error.
        error (type[Exception]): Programming error outside the domain exception types.

    Returns:
        None: Confirms programming errors propagate instead of becoming validation rows.
    """
    monkeypatch.setattr(
        market_smoke,
        "create_market",
        Mock(side_effect=error("Unexpected implementation error")),
    )
    with pytest.raises(error, match="Unexpected implementation error"):
        market_smoke.main([])


def test_input_immutability_failure_is_not_reported_as_complete(
    market_smoke: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep the final validation stage and overall outcome distinct on failure.

    Args:
        market_smoke (ModuleType): Shared synthetic entry point.
        monkeypatch (pytest.MonkeyPatch): Supply unequal before/after fingerprints.
        capsys (pytest.CaptureFixture[str]): Captured report streams.

    Returns:
        None: Confirms completed checks cannot produce an overall PASS after mutation.
    """
    original = market_smoke.fingerprint_inputs(market_smoke.create_market())
    monkeypatch.setattr(
        market_smoke,
        "fingerprint_inputs",
        Mock(side_effect=[original, (*original, "changed")]),
    )
    assert market_smoke.main([]) == 1
    captured = capsys.readouterr()
    assert "Input immutability" in captured.err
    assert "Simulation mutated inputs" in captured.err
    assert "Overall: FAIL" in captured.err
    assert "Overall: PASS" not in captured.out + captured.err
