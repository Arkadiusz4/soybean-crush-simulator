from importlib.metadata import version
from pathlib import Path

import pytest
from typer.testing import CliRunner

from soybean_sim.cli import app

runner = CliRunner()


def test_help_lists_supported_commands() -> None:
    """Check that CLI help succeeds and lists the two implemented commands."""
    result = runner.invoke(app, ["--help"], env={"TERM": "dumb"})
    assert result.exit_code == 0
    assert "version" in result.stdout
    assert "validate-config" in result.stdout


def test_version_uses_installed_metadata() -> None:
    """Check that the version command reports installed package metadata."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == version("soybean-crush-simulator")


def test_validate_sample_config(baseline_path: Path) -> None:
    """Check that CLI validation confirms the baseline file and prints its path."""
    result = runner.invoke(app, ["validate-config", str(baseline_path)])
    assert result.exit_code == 0
    assert "Configuration is valid" in result.stdout
    assert str(baseline_path) in result.stdout


@pytest.mark.parametrize(
    ("contents", "expected"),
    [("regions: [", "Invalid YAML"), ("duration_steps: 0", "duration_steps")],
)
def test_invalid_config_returns_actionable_error(
    tmp_path: Path, contents: str, expected: str
) -> None:
    """Check that invalid settings produce a useful error and no success message."""
    path = tmp_path / "invalid.yaml"
    path.write_text(contents, encoding="utf-8")
    result = runner.invoke(app, ["validate-config", str(path)])
    assert result.exit_code == 1
    assert expected in result.stderr
    assert str(path) in result.stderr
    assert "Configuration is valid" not in result.stdout
    assert "Traceback" not in result.output


def test_missing_file_returns_error(tmp_path: Path) -> None:
    """Check that a missing configuration exits unsuccessfully with its path."""
    path = tmp_path / "missing.yaml"
    result = runner.invoke(app, ["validate-config", str(path)])
    assert result.exit_code == 1
    assert "Cannot read" in result.stderr
    assert str(path) in result.stderr


def test_validate_config_requires_path() -> None:
    """Check that omitting the configuration path produces a CLI usage error."""
    result = runner.invoke(app, ["validate-config"])
    assert result.exit_code == 2


def test_no_arguments_shows_usage() -> None:
    """Check that an empty invocation shows usage and returns a usage error."""
    result = runner.invoke(app, [])
    assert result.exit_code == 2
    assert "Usage" in result.output
