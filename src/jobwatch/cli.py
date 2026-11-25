"""Interface en ligne de commande (typer)."""

from typing import Annotated

import typer

from jobwatch import __version__

app = typer.Typer(
    name="jobwatch",
    help="Veille quotidienne des offres France Travail (CDI DevOps / Cloud / Run).",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"jobwatch {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Affiche la version."
        ),
    ] = False,
) -> None:
    """Veille quotidienne des offres France Travail (CDI DevOps / Cloud / Run)."""
