from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from popctl.advisor import (
    AgentRunner,
    cleanup_empty_sessions,
    delete_session,
    find_all_unapplied_decisions,
    get_session_manager,
    import_decisions,
)
from popctl.advisor.config import (
    ProviderChoice,
    load_or_create_config,
)
from popctl.advisor.exchange import (
    DecisionsResult,
    apply_decisions_to_manifest,
    record_advisor_apply_to_history,
)
from popctl.advisor.runner import MANUAL_MODE_SENTINEL
from popctl.advisor.scanning import scan_system
from popctl.advisor.workspace import create_session_workspace, ensure_advisor_sessions_dir
from popctl.cli.types import require_manifest
from popctl.core.manifest import (
    ManifestError,
    save_manifest,
)
from popctl.core.paths import get_manifest_path, get_state_dir
from popctl.utils.formatting import (
    console,
    print_error,
    print_info,
    print_success,
)

app = typer.Typer(
    name="advisor",
    help="AI-assisted package classification.",
    invoke_without_command=True,
    no_args_is_help=True,
)


def _prepare_session(
    provider: ProviderChoice | None,
    model: str | None,
    input_file: Path | None,
) -> tuple[AgentRunner, Path]:
    config = load_or_create_config(provider.value if provider else None, model)
    print_info(f"Using provider: {config.provider}, model: {config.effective_model}")

    try:
        scan_result = scan_system(input_file)
    except RuntimeError as e:
        print_error(str(e))
        raise typer.Exit(code=1) from None

    sessions_dir = ensure_advisor_sessions_dir()
    manifest_path = get_manifest_path()
    memory_path = get_state_dir() / "advisor" / "memory.md"
    workspace_dir = create_session_workspace(
        scan_result,
        sessions_dir,
        manifest_path=manifest_path if manifest_path.exists() else None,
        memory_path=memory_path if memory_path.exists() else None,
    )
    session = get_session_manager()
    return AgentRunner(config, session=session), workspace_dir


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
    agent_runner, workspace_dir = _prepare_session(provider, model, input_file)
    print_info(f"Workspace: {workspace_dir}")

    print_info("Running AI agent in headless mode...")
    console.print()

    result = agent_runner.run_headless(workspace_dir)

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
    agent_runner, workspace_dir = _prepare_session(provider, model, input_file)
    print_info(f"Session workspace: {workspace_dir}")

    result = agent_runner.launch_interactive(workspace_dir)

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

    Finds ALL unapplied decisions across sessions and applies them
    idempotently. Each session is marked as applied after processing.

    Examples:
        popctl advisor apply              # Apply all unapplied sessions
        popctl advisor apply --dry-run    # Preview only
        popctl advisor apply -i dec.toml  # From specific file
    """
    # Clean up empty sessions first
    sessions_dir = ensure_advisor_sessions_dir()
    cleaned = cleanup_empty_sessions(sessions_dir)
    if cleaned:
        print_info(f"Cleaned up {cleaned} empty session(s).")

    # Step 1: Collect decisions to apply
    if input_file is not None:
        decisions_paths = [input_file]
    else:
        decisions_paths = find_all_unapplied_decisions(sessions_dir)
        if not decisions_paths:
            print_error(
                "No unapplied advisor decisions found. Run 'popctl advisor classify' first."
            )
            raise typer.Exit(code=1)

    print_info(f"Found {len(decisions_paths)} unapplied decision file(s).")

    # Step 2: Load current manifest
    manifest = require_manifest()

    # Step 3: Apply all decisions in chronological order
    total_keep = 0
    total_remove = 0
    total_ask = 0
    all_ask_packages: list[tuple[str, str, str, float]] = []
    all_decisions_for_history: list[tuple[Path, DecisionsResult]] = []

    for decisions_path in decisions_paths:
        print_info(f"Applying: {decisions_path}")

        try:
            decisions = import_decisions(decisions_path)
        except FileNotFoundError:
            print_error(f"  decisions.toml not found at {decisions_path}")
            continue
        except ValueError as e:
            print_error(f"  Invalid decisions.toml: {e}")
            continue

        stats, ask_packages = apply_decisions_to_manifest(manifest, decisions)

        for _source, counts in stats.items():
            total_keep += counts["keep"]
            total_remove += counts["remove"]
            total_ask += counts["ask"]

        all_ask_packages.extend(ask_packages)
        all_decisions_for_history.append((decisions_path, decisions))

    if not all_decisions_for_history:
        print_error("All decision files failed to load. Nothing applied.")
        raise typer.Exit(code=1)

    # Step 4: Display summary
    console.print()

    table = Table(title="Classification Summary (All Sessions)", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right")
    table.add_row("Keep", str(total_keep), style="green")
    table.add_row("Remove", str(total_remove), style="red")
    table.add_row("Ask", str(total_ask), style="yellow")
    table.add_row("Sessions", str(len(decisions_paths)), style="bold")
    console.print(table)
    console.print()

    if all_ask_packages:
        console.print("[yellow]Packages requiring manual decision:[/yellow]")
        for name, source, reason, confidence in all_ask_packages:
            console.print(f"  [dim]-[/dim] {name} ({source}): {reason} [{confidence:.2f}]")
        console.print()
        console.print(
            "[dim]Run 'popctl advisor classify' again to re-evaluate, "
            "or manually add to manifest.[/dim]"
        )
        console.print()

    # Step 5: Save manifest and mark sessions (unless dry-run)
    manifest_path = get_manifest_path()

    if dry_run:
        console.print(f"[cyan][dry-run][/cyan] Would update manifest at {manifest_path}")
    else:
        manifest.meta.updated = datetime.now(UTC)

        try:
            save_manifest(manifest)
            print_success(f"Manifest updated at {manifest_path}")
        except ManifestError as e:
            print_error(f"Failed to save manifest: {e}")
            raise typer.Exit(code=1) from e

        # Delete ephemeral sessions and record to history
        for decisions_path, decisions in all_decisions_for_history:
            delete_session(decisions_path)
            record_advisor_apply_to_history(decisions)

        print_info(f"{len(all_decisions_for_history)} session(s) applied and recorded to history.")
