"""Main CLI application entry point.

Defines the Typer application and global options.
"""

from typing import Annotated

import typer

from popctl import __version__
from popctl.cli.commands import advisor, apply, diff, fs, history, init, scan, sync, undo

# Create main Typer app
app = typer.Typer(
    name="popctl",
    help="Declarative system configuration for Pop!_OS.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"popctl version {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-V",
            callback=version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Enable verbose output.",
        ),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet",
            "-q",
            help="Suppress non-essential output.",
        ),
    ] = False,
) -> None:
    """popctl - Declarative system configuration for Pop!_OS.

    Define your desired system state in a manifest file and
    automatically maintain that state over time.
    """
    # Store options in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet


# Register commands
app.add_typer(scan.app, name="scan")
app.add_typer(init.app, name="init")
app.add_typer(diff.app, name="diff")
app.add_typer(apply.app, name="apply")
app.add_typer(advisor.app, name="advisor")
app.add_typer(sync.app, name="sync")
app.add_typer(history.app, name="history")
app.add_typer(undo.app, name="undo")
app.add_typer(fs.app, name="fs")


if __name__ == "__main__":
    app()
