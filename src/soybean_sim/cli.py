"""Command-line interface for package information and configuration validation."""

from pathlib import Path
from typing import Annotated

import typer

from soybean_sim import __version__
from soybean_sim.config import ConfigurationError, load_config

app = typer.Typer(
    help="Soybean Crush Market Simulator project tools.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


@app.command()
def version() -> None:
    """Show the installed package version."""
    typer.echo(__version__)


@app.command()
def validate_config(
    path: Annotated[Path, typer.Argument(help="Path to a UTF-8 YAML configuration.")],
) -> None:
    """Validate a configuration file without running a simulation."""
    try:
        load_config(path)
    except ConfigurationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Configuration is valid: {path}")
