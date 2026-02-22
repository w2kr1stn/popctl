"""Advisor commands for AI-assisted package classification.

This module provides CLI commands for the Claude Advisor feature,
which uses AI agents (Claude Code or Gemini CLI) to classify packages
as keep, remove, or ask.

Commands:
- classify: Headless batch classification
- session: Interactive AI session
- apply: Apply classification decisions to manifest
"""

import json
import logging
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from popctl.advisor import (
    AdvisorConfig,
    AgentRunner,
    DecisionsResult,
    create_session_workspace,
    find_latest_decisions,
    import_decisions,
)
from popctl.advisor.config import (
    AdvisorConfigError,
    load_advisor_config,
    save_advisor_config,
)
from popctl.advisor.exchange import EXCHANGE_DIR, apply_decisions_to_manifest
from popctl.advisor.runner import MANUAL_MODE_SENTINEL
from popctl.advisor.workspace import ensure_advisor_sessions_dir
from popctl.cli.types import get_available_scanners
from popctl.core.manifest import (
    ManifestError,
    ManifestNotFoundError,
    load_manifest,
    save_manifest,
)
from popctl.core.paths import get_manifest_path, get_state_dir
from popctl.core.state import record_action
from popctl.models.history import (
    HistoryActionType,
    HistoryItem,
    create_history_entry,
)
from popctl.models.package import (
    PACKAGE_SOURCE_KEYS,
    PackageSource,
    PackageStatus,
    ScannedPackage,
)
from popctl.models.scan_result import ScanResult
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


def load_or_create_config(
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
    except AdvisorConfigError:
        config = AdvisorConfig()
        try:
            save_advisor_config(config)
            print_info("Created default advisor configuration.")
        except AdvisorConfigError as e:
            print_warning(f"Could not save default config: {e}")

    # Apply CLI overrides
    if provider is not None or model is not None:
        updates: dict[str, str] = {}
        if provider is not None:
            updates["provider"] = provider.value
        if model is not None:
            updates["model"] = model
        config = config.model_copy(update=updates)

    return config


def scan_system(input_file: Path | None = None) -> ScanResult:
    """Scan system for packages or load from file.

    Args:
        input_file: Optional path to existing scan.json file.

    Returns:
        ScanResult with package data.

    Raises:
        typer.Exit: If scanning fails or input file is invalid.
    """
    if input_file is not None:
        # Load from existing scan file
        if not input_file.exists():
            print_error(f"Input file not found: {input_file}")
            raise typer.Exit(code=1)

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

    # Perform live scan using all available scanners
    scanners = get_available_scanners()
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


def record_advisor_apply_to_history(
    decisions: DecisionsResult,
) -> None:
    """Record advisor apply decisions to history.

    Creates a single history entry for all classifications applied.
    Errors during recording are logged but do not interrupt the flow.

    Args:
        decisions: The decisions result that was applied.
    """
    _logger = logging.getLogger(__name__)

    try:
        # Collect all items from decisions
        items: list[HistoryItem] = []

        for source_str in PACKAGE_SOURCE_KEYS:
            source_decisions = decisions.packages.get(source_str)  # type: ignore[arg-type]
            if source_decisions is None:
                continue

            pkg_source = PackageSource(source_str)

            # Add keep decisions
            for decision in source_decisions.keep:
                items.append(HistoryItem(name=decision.name, source=pkg_source))

            # Add remove decisions
            for decision in source_decisions.remove:
                items.append(HistoryItem(name=decision.name, source=pkg_source))

        if items:
            entry = create_history_entry(
                action_type=HistoryActionType.ADVISOR_APPLY,
                items=items,
                metadata={"command": "popctl advisor apply"},
            )
            record_action(entry)
            _logger.debug("Recorded %d advisor apply item(s) to history", len(items))

    except (OSError, RuntimeError) as e:
        _logger.warning("Failed to record advisor apply to history: %s", str(e))
        print_warning(f"Could not record classifications to history: {e}")


def _create_workspace(scan_result: ScanResult) -> Path:
    """Create a session workspace for the scan result.

    Args:
        scan_result: Scan result with package data.

    Returns:
        Path to the created workspace directory.
    """
    sessions_dir = ensure_advisor_sessions_dir()
    manifest_path = get_manifest_path()
    manifest_for_workspace = manifest_path if manifest_path.exists() else None

    memory_path = get_state_dir() / "advisor" / "memory.md"
    memory_for_workspace = memory_path if memory_path.exists() else None

    return create_session_workspace(
        scan_result,
        sessions_dir,
        manifest_path=manifest_for_workspace,
        memory_path=memory_for_workspace,
    )


@app.command()
def classify(
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
    """Classify packages using AI assistance (headless mode).

    Scans the system, creates a session workspace, and runs the AI agent
    autonomously. After classification, run 'popctl advisor apply'.

    Examples:
        popctl advisor classify              # Headless classification
        popctl advisor classify -p gemini    # Use Gemini
        popctl advisor classify -m opus      # Use Claude Opus
    """
    config = load_or_create_config(provider, model)
    print_info(f"Using provider: {config.provider}, model: {config.effective_model}")

    scan_result = scan_system(input_file)
    workspace_dir = _create_workspace(scan_result)
    print_info(f"Workspace: {workspace_dir}")

    print_info("Running AI agent in headless mode...")
    console.print()

    runner = AgentRunner(config)
    result = runner.run_headless(workspace_dir)

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


@app.command()
def session(
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
    """Start an interactive AI session for package classification.

    Prepares a workspace with CLAUDE.md and scan data, then launches
    Claude Code interactively. After the session, run 'popctl advisor apply'.

    Examples:
        popctl advisor session               # Interactive session
        popctl advisor session -p gemini     # Use Gemini
    """
    config = load_or_create_config(provider, model)
    print_info(f"Using provider: {config.provider}, model: {config.effective_model}")

    scan_result = scan_system(input_file)
    workspace_dir = _create_workspace(scan_result)
    print_info(f"Session workspace: {workspace_dir}")

    runner = AgentRunner(config)
    result = runner.launch_interactive(workspace_dir)

    if result.success:
        print_success("Session completed.")
        if result.decisions_path:
            print_info(f"Decisions written to: {result.decisions_path}")
            print_info("Run 'popctl advisor apply' to apply the classifications.")
    elif result.error == MANUAL_MODE_SENTINEL:
        console.print()
        console.print(result.output)
    else:
        print_error(f"Session failed: {result.error}")
        raise typer.Exit(code=1)


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
            help="Path to decisions.toml (default: latest session).",
        ),
    ] = None,
) -> None:
    """Apply AI classification decisions to manifest.

    Reads decisions.toml from the last classification session and updates
    the manifest accordingly.

    Examples:
        popctl advisor apply              # Apply from latest session
        popctl advisor apply --dry-run    # Preview only
        popctl advisor apply -i dec.toml  # From specific file
    """
    # Step 1: Determine decisions.toml path
    if input_file is not None:
        decisions_path = input_file
    else:
        # Try latest session workspace first, then legacy exchange dir
        sessions_dir = ensure_advisor_sessions_dir()
        latest = find_latest_decisions(sessions_dir)
        decisions_path = latest if latest is not None else EXCHANGE_DIR / "decisions.toml"

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
    stats, ask_packages = apply_decisions_to_manifest(manifest, decisions)

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

        # Record to history
        record_advisor_apply_to_history(decisions)
        print_info("Classifications recorded to history.")
