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
    """Print the installed package version to standard output."""
    typer.echo(__version__)


@app.command()
def validate_config(
    path: Annotated[Path, typer.Argument(help="Path to a UTF-8 YAML configuration.")],
) -> None:
    """Validate a local YAML file and print confirmation on success."""
    try:
        load_config(path)
    except ConfigurationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Configuration is valid: {path}")
