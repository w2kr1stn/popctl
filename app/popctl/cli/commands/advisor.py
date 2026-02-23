"""Advisor commands for AI-assisted package classification.

This module provides CLI commands for the Claude Advisor feature,
which uses AI agents (Claude Code or Gemini CLI) to classify packages
as keep, remove, or ask.

Two execution modes are supported:
- Interactive (default): Prepares files and shows instructions for manual agent execution
- Headless (--auto): Runs the AI agent autonomously for classification
"""

from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from popctl.advisor import (
    AdvisorConfig,
    AgentRunner,
    export_prompt_files,
    export_scan_for_advisor,
    is_running_in_container,
)
from popctl.advisor.config import (
    AdvisorConfigError,
    AdvisorConfigNotFoundError,
    get_default_config,
    load_advisor_config,
    save_advisor_config,
)
from popctl.core.paths import ensure_exchange_dir, get_manifest_path
from popctl.models.scan_result import ScanResult
from popctl.scanners.apt import AptScanner
from popctl.scanners.flatpak import FlatpakScanner
from popctl.utils.formatting import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
)

app = typer.Typer(
    name="advisor",
    help="AI-assisted package classification.",
    invoke_without_command=True,
    no_args_is_help=True,
)


class ProviderChoice(str, Enum):
    """Available AI providers."""

    CLAUDE = "claude"
    GEMINI = "gemini"


def _show_container_warning() -> None:
    """Display warning if running inside a container."""
    if is_running_in_container():
        print_warning(
            "popctl is running inside a container.\n"
            "    Package scanning and system modifications may not work correctly.\n"
            "    Consider running popctl directly on the host system."
        )


def _load_or_create_config(
    provider: ProviderChoice | None = None,
    model: str | None = None,
) -> AdvisorConfig:
    """Load existing config or create default with CLI overrides.

    Args:
        provider: Optional provider override from CLI.
        model: Optional model override from CLI.

    Returns:
        AdvisorConfig with applied overrides.
    """
    try:
        config = load_advisor_config()
    except AdvisorConfigNotFoundError:
        # Create default config if not found
        config = get_default_config()
        # Save the default config for future use
        try:
            save_advisor_config(config)
            print_info("Created default advisor configuration.")
        except AdvisorConfigError as e:
            print_warning(f"Could not save default config: {e}")
    except AdvisorConfigError as e:
        print_warning(f"Error loading config, using defaults: {e}")
        config = get_default_config()

    # Apply CLI overrides by creating a new config with specific fields
    if provider is not None or model is not None:
        config = AdvisorConfig(
            provider=provider.value if provider is not None else config.provider,
            model=model if model is not None else config.model,
            dev_script=config.dev_script,
            timeout_seconds=config.timeout_seconds,
        )

    return config


def _scan_system(input_file: Path | None = None) -> ScanResult:
    """Scan system for packages or load from file.

    Args:
        input_file: Optional path to existing scan.json file.

    Returns:
        ScanResult with package data.

    Raises:
        typer.Exit: If scanning fails or input file is invalid.
    """
    from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
    from popctl.scanners.base import Scanner

    if input_file is not None:
        # Load from existing scan file
        if not input_file.exists():
            print_error(f"Input file not found: {input_file}")
            raise typer.Exit(code=1)

        import json

        try:
            data = json.loads(input_file.read_text())
            packages: list[ScannedPackage] = []
            sources_set: set[str] = set()

            for pkg_data in data.get("packages", []):
                pkg = ScannedPackage(
                    name=pkg_data["name"],
                    source=PackageSource(pkg_data["source"]),
                    version=pkg_data["version"],
                    status=PackageStatus(pkg_data["status"]),
                    description=pkg_data.get("description"),
                    size_bytes=pkg_data.get("size_bytes"),
                )
                packages.append(pkg)
                sources_set.add(pkg_data["source"])

            return ScanResult.create(packages, list(sources_set))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print_error(f"Invalid scan file format: {e}")
            raise typer.Exit(code=1) from e

    # Perform live scan
    scanners: list[Scanner] = []
    apt_scanner = AptScanner()
    flatpak_scanner = FlatpakScanner()

    if apt_scanner.is_available():
        scanners.append(apt_scanner)
    else:
        print_warning("APT package manager is not available.")

    if flatpak_scanner.is_available():
        scanners.append(flatpak_scanner)
    else:
        print_warning("Flatpak is not available.")

    if not scanners:
        print_error("No package managers are available on this system.")
        raise typer.Exit(code=1)

    packages: list[ScannedPackage] = []
    sources: list[str] = []

    for scanner in scanners:
        sources.append(scanner.source.value)
        try:
            for pkg in scanner.scan():
                packages.append(pkg)
        except RuntimeError as e:
            print_error(f"Scan failed: {e}")
            raise typer.Exit(code=1) from e

    print_info(f"Scanned {len(packages)} packages from {len(sources)} source(s).")
    return ScanResult.create(packages, sources)


def _show_interactive_instructions(exchange_dir: Path, config: AdvisorConfig) -> None:
    """Display instructions for interactive mode.

    Args:
        exchange_dir: Path to exchange directory.
        config: Advisor configuration.
    """
    runner = AgentRunner(config)
    instructions = runner.prepare_interactive(exchange_dir)

    console.print()
    console.print("[bold]Interactive Mode[/bold]")
    console.print()
    console.print(instructions)


@app.command()
def classify(
    auto: Annotated[
        bool,
        typer.Option(
            "--auto",
            "-a",
            help="Headless mode: run classification autonomously.",
        ),
    ] = False,
    provider: Annotated[
        ProviderChoice | None,
        typer.Option(
            "--provider",
            "-p",
            help="AI provider to use (claude or gemini).",
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help="Model to use (e.g., sonnet, opus, gemini-2.5-pro).",
        ),
    ] = None,
    input_file: Annotated[
        Path | None,
        typer.Option(
            "--input",
            "-i",
            help="Use existing scan.json instead of scanning.",
        ),
    ] = None,
) -> None:
    """Classify packages using AI assistance.

    Default: Interactive mode - prepares files and shows instructions.
    With --auto: Headless mode - runs classification autonomously.

    Examples:
        popctl advisor classify              # Interactive mode
        popctl advisor classify --auto       # Headless mode
        popctl advisor classify -p gemini    # Use Gemini
        popctl advisor classify -m opus      # Use Claude Opus
    """
    # Step 1: Check container warning
    _show_container_warning()

    # Step 2: Load/create config with CLI overrides
    config = _load_or_create_config(provider, model)
    print_info(f"Using provider: {config.provider}, model: {config.effective_model}")

    # Step 3: Scan system or load from file
    scan_result = _scan_system(input_file)

    # Step 4: Ensure exchange directory exists
    exchange_dir = ensure_exchange_dir()
    print_info(f"Exchange directory: {exchange_dir}")

    # Step 5: Export scan data for advisor
    manifest_path = get_manifest_path()
    manifest_for_export = manifest_path if manifest_path.exists() else None
    scan_json_path = export_scan_for_advisor(scan_result, exchange_dir, manifest_for_export)
    print_success(f"Exported scan data to: {scan_json_path}")

    # Step 6: Export prompt files
    prompt_path, instructions_path = export_prompt_files(
        exchange_dir,
        manifest_path=manifest_for_export,
        headless=auto,
    )
    print_success(f"Exported prompt to: {prompt_path}")
    if instructions_path:
        print_success(f"Exported instructions to: {instructions_path}")

    if auto:
        # Headless mode: run agent autonomously
        print_info("Running AI agent in headless mode...")
        console.print()

        runner = AgentRunner(config)
        result = runner.run_headless(prompt_path, exchange_dir)

        if result.success:
            print_success("Classification completed successfully.")
            if result.decisions_path:
                print_info(f"Decisions written to: {result.decisions_path}")
            if result.output:
                console.print()
                console.print("[dim]Agent output:[/dim]")
                console.print(result.output[:500])  # Limit output display
            console.print()
            print_info("Run 'popctl advisor apply' to apply the classifications.")
        else:
            print_error(f"Classification failed: {result.error}")
            if result.output:
                console.print()
                console.print("[dim]Agent output:[/dim]")
                console.print(result.output[:500])
            raise typer.Exit(code=1)
    else:
        # Interactive mode: show instructions
        _show_interactive_instructions(exchange_dir, config)


@app.command()
def apply(
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="Preview changes without modifying manifest.",
        ),
    ] = False,
    input_file: Annotated[
        Path | None,
        typer.Option(
            "--input",
            "-i",
            help="Path to decisions.toml (default: exchange dir).",
        ),
    ] = None,
) -> None:
    """Apply AI classification decisions to manifest.

    Reads decisions.toml from the last classification and updates
    the manifest accordingly.

    Examples:
        popctl advisor apply              # Apply from exchange dir
        popctl advisor apply --dry-run    # Preview only
        popctl advisor apply -i dec.toml  # From specific file
    """
    from datetime import UTC, datetime

    from rich.table import Table

    from popctl.advisor import import_decisions
    from popctl.core.manifest import (
        ManifestError,
        ManifestNotFoundError,
        load_manifest,
        save_manifest,
    )
    from popctl.core.paths import get_exchange_dir
    from popctl.models.manifest import PackageEntry

    # Step 1: Determine decisions.toml path
    decisions_path = input_file if input_file is not None else get_exchange_dir() / "decisions.toml"

    print_info(f"Decisions from: {decisions_path}")

    # Step 2: Load decisions
    # import_decisions expects the directory containing decisions.toml
    try:
        decisions = import_decisions(decisions_path.parent)
    except FileNotFoundError as err:
        print_error(f"decisions.toml not found at {decisions_path}")
        print_info("Run 'popctl advisor classify' first to generate classifications.")
        raise typer.Exit(code=1) from err
    except ValueError as e:
        print_error(f"Invalid decisions.toml: {e}")
        raise typer.Exit(code=1) from e

    # Step 3: Load current manifest
    try:
        manifest = load_manifest()
    except ManifestNotFoundError as err:
        print_error("No manifest found. Run 'popctl init' first to create a manifest.")
        raise typer.Exit(code=1) from err
    except ManifestError as e:
        print_error(f"Failed to load manifest: {e}")
        raise typer.Exit(code=1) from e

    # Step 4: Apply decisions and collect statistics
    stats: dict[str, dict[str, int]] = {}
    ask_packages: list[tuple[str, str, str, float]] = []  # (name, source, reason, confidence)

    for source in ("apt", "flatpak"):
        source_decisions = decisions.packages.get(source)
        if source_decisions is None:
            continue

        stats[source] = {"keep": 0, "remove": 0, "ask": 0}

        # Process keep decisions
        for decision in source_decisions.keep:
            manifest.packages.keep[decision.name] = PackageEntry(
                source=source,  # type: ignore[arg-type]
                status="keep",
                reason=decision.reason,
            )
            stats[source]["keep"] += 1

        # Process remove decisions
        for decision in source_decisions.remove:
            manifest.packages.remove[decision.name] = PackageEntry(
                source=source,  # type: ignore[arg-type]
                status="remove",
                reason=decision.reason,
            )
            stats[source]["remove"] += 1

        # Process ask decisions (skip but collect for display)
        for decision in source_decisions.ask:
            ask_packages.append((decision.name, source, decision.reason, decision.confidence))
            stats[source]["ask"] += 1

    # Step 5: Display summary
    console.print()

    # Create summary table
    table = Table(title="Classification Summary", show_header=True)
    table.add_column("Source", style="cyan")
    table.add_column("Keep", style="green", justify="right")
    table.add_column("Remove", style="red", justify="right")
    table.add_column("Ask", style="yellow", justify="right")

    total_keep = 0
    total_remove = 0
    total_ask = 0

    for source, counts in stats.items():
        table.add_row(
            source.upper(),
            str(counts["keep"]),
            str(counts["remove"]),
            str(counts["ask"]),
        )
        total_keep += counts["keep"]
        total_remove += counts["remove"]
        total_ask += counts["ask"]

    if stats:
        table.add_row("", "", "", "", style="dim")
        table.add_row(
            "Total",
            str(total_keep),
            str(total_remove),
            str(total_ask),
            style="bold",
        )

    console.print(table)
    console.print()

    # Show packages requiring manual decision
    if ask_packages:
        console.print("[yellow]Packages requiring manual decision:[/yellow]")
        for name, source, reason, confidence in ask_packages:
            console.print(f"  [dim]-[/dim] {name} ({source}): {reason} [{confidence:.2f}]")
        console.print()
        console.print(
            "[dim]Run 'popctl advisor classify' again to re-evaluate, "
            "or manually add to manifest.[/dim]"
        )
        console.print()

    # Step 6: Save manifest (unless dry-run)
    manifest_path = get_manifest_path()

    if dry_run:
        console.print(f"[cyan][dry-run][/cyan] Would update manifest at {manifest_path}")
    else:
        # Update manifest timestamp
        manifest.meta.updated = datetime.now(UTC)

        try:
            save_manifest(manifest)
            print_success(f"Manifest updated at {manifest_path}")
        except ManifestError as e:
            print_error(f"Failed to save manifest: {e}")
            raise typer.Exit(code=1) from e


@app.callback(invoke_without_command=True)
def advisor_callback(ctx: typer.Context) -> None:
    """AI-assisted package classification.

    The advisor uses AI agents (Claude Code or Gemini CLI) to classify
    packages as keep, remove, or ask. Run 'popctl advisor classify --help'
    for usage details.
    """
    # Show help if no subcommand
    if ctx.invoked_subcommand is None:
        ctx.get_help()
