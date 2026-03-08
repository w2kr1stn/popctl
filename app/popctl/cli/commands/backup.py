"""Backup and restore CLI commands.

Provides commands to create encrypted system snapshots, restore them
on fresh installations, and manage existing backups.
"""

from typing import Annotated

import typer

from popctl.utils.formatting import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
)

app = typer.Typer(
    help="Backup and restore system configuration.",
    no_args_is_help=True,
)


@app.command()
def create(
    target: Annotated[
        str,
        typer.Option(
            "--target",
            "-t",
            help="Backup destination: local path or rclone remote (e.g. 'gdrive:backups/').",
        ),
    ] = "",
    recipient: Annotated[
        str | None,
        typer.Option(
            "--recipient",
            "-r",
            help="age public key or path to recipients file for encryption.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="Show what would be backed up without creating archive.",
        ),
    ] = False,
) -> None:
    """Create an encrypted backup of the current system state.

    Walks the home directory (excluding symlink dirs and caches),
    collects popctl state, compresses with zstd, and encrypts with age.

    Examples:
        popctl backup create -r age1abc...xyz
        popctl backup create -t /mnt/usb/backups -r ~/.config/popctl/backup.age-recipients
        popctl backup create -t gdrive:popctl-backups/ -r age1abc...xyz
        popctl backup create --dry-run
    """
    from popctl.backup.backup import BackupError, collect_backup_files, create_backup

    if dry_run:
        try:
            files = collect_backup_files()
        except BackupError as e:
            print_error(str(e))
            raise typer.Exit(code=1) from e

        total_size = sum(p.stat().st_size for p, _ in files if p.exists())
        console.print(f"\n[bold]Backup dry-run:[/bold] {len(files)} files, "
                       f"{total_size / 1024 / 1024:.1f} MB uncompressed")

        # Show category breakdown
        categories: dict[str, int] = {}
        for _, archive_path in files:
            cat = archive_path.split("/")[1] if "/" in archive_path else "other"
            categories[cat] = categories.get(cat, 0) + 1
        for cat, count in sorted(categories.items()):
            console.print(f"  {cat}: {count} files")

        print_info("Dry-run mode: No archive was created.")
        return

    try:
        dest = create_backup(target=target, recipient=recipient)
    except BackupError as e:
        print_error(str(e))
        raise typer.Exit(code=1) from e

    print_success(f"Backup created: {dest}")


@app.command()
def restore(
    source: Annotated[
        str,
        typer.Argument(
            help="Backup file: local path or rclone remote.",
        ),
    ],
    identity: Annotated[
        str | None,
        typer.Option(
            "--identity",
            "-i",
            help="Path to age identity (private key) file for decryption.",
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip confirmation prompt.",
        ),
    ] = False,
    files_only: Annotated[
        bool,
        typer.Option(
            "--files-only",
            help="Restore config files only, skip package installation.",
        ),
    ] = False,
    packages_only: Annotated[
        bool,
        typer.Option(
            "--packages-only",
            help="Install packages only, skip file restoration.",
        ),
    ] = False,
) -> None:
    """Restore a system from an encrypted backup.

    Decrypts the archive, restores all files to their original
    locations, and installs/removes packages to match the manifest.

    Examples:
        popctl backup restore /mnt/usb/popctl-backup-host-20260306-120000.tar.zst.age
        popctl backup restore gdrive:backups/backup.tar.zst.age -i ~/.age/key.txt
        popctl backup restore backup.tar.zst.age --files-only
        popctl backup restore backup.tar.zst.age --packages-only -y
    """
    from popctl.backup.backup import BackupError
    from popctl.backup.restore import read_backup_metadata, restore_backup

    if files_only and packages_only:
        print_error("Cannot use --files-only and --packages-only together.")
        raise typer.Exit(code=1)

    # Read metadata for confirmation
    try:
        metadata = read_backup_metadata(source, identity)
    except BackupError as e:
        print_error(str(e))
        raise typer.Exit(code=1) from e

    console.print("\n[bold]Backup info:[/bold]")
    console.print(f"  Created:  {metadata.created}")
    console.print(f"  Hostname: {metadata.hostname}")
    console.print(f"  Version:  {metadata.popctl_version}")

    mode = "files only" if files_only else "packages only" if packages_only else "full restore"
    console.print(f"  Mode:     {mode}")

    if not yes:
        confirmed = typer.confirm(f"\nProceed with {mode}?", default=False)
        if not confirmed:
            print_info("Aborted.")
            raise typer.Exit(code=0)

    try:
        counts = restore_backup(
            source,
            identity,
            files_only=files_only,
            packages_only=packages_only,
        )
    except BackupError as e:
        print_error(str(e))
        raise typer.Exit(code=1) from e

    # Summary
    console.print()
    if counts["popctl_state"]:
        print_success(f"popctl state: {counts['popctl_state']} file(s) restored")
    if counts["home_files"]:
        print_success(f"Home files: {counts['home_files']} file(s) restored")
    if counts["packages_installed"]:
        print_success(f"Packages: {counts['packages_installed']} installed")
    if counts["packages_failed"]:
        print_warning(f"Packages: {counts['packages_failed']} failed")

    if counts["packages_failed"]:
        raise typer.Exit(code=1)


@app.command(name="list")
def list_backups(
    target: Annotated[
        str,
        typer.Option(
            "--target",
            "-t",
            help="Backup location to scan: local path or rclone remote.",
        ),
    ] = "",
) -> None:
    """List available backups at the target location.

    Reads filenames only — no decryption needed.

    Examples:
        popctl backup list
        popctl backup list -t /mnt/usb/backups
        popctl backup list -t gdrive:popctl-backups/
    """
    from popctl.backup.backup import BackupError
    from popctl.backup.config import load_backup_config
    from popctl.backup.restore import list_backups as _list_backups

    effective_target = target or load_backup_config().target
    try:
        backups = _list_backups(effective_target)
    except BackupError as e:
        print_error(str(e))
        raise typer.Exit(code=1) from e

    if not backups:
        print_info("No backups found.")
        return

    console.print(f"\n[bold]Available backups ({len(backups)}):[/bold]")
    for name in backups:
        # Parse hostname and timestamp from filename
        # Format: popctl-backup-{hostname}-{YYYYMMDD-HHMMSS}.tar.zst.age
        parts = name.replace(".tar.zst.age", "").split("-", 3)
        if len(parts) >= 4:
            hostname = parts[2]
            timestamp = parts[3]
            console.print(f"  {name}  [dim]({hostname}, {timestamp})[/dim]")
        else:
            console.print(f"  {name}")


@app.command()
def info(
    source: Annotated[
        str,
        typer.Argument(
            help="Backup file: local path or rclone remote.",
        ),
    ],
    identity: Annotated[
        str | None,
        typer.Option(
            "--identity",
            "-i",
            help="Path to age identity (private key) for decryption.",
        ),
    ] = None,
) -> None:
    """Show backup metadata without restoring.

    Decrypts only metadata.json from the archive.

    Examples:
        popctl backup info /mnt/usb/popctl-backup-host-20260306-120000.tar.zst.age
        popctl backup info backup.tar.zst.age -i ~/.age/key.txt
    """
    from popctl.backup.backup import BackupError
    from popctl.backup.restore import read_backup_metadata

    try:
        metadata = read_backup_metadata(source, identity)
    except BackupError as e:
        print_error(str(e))
        raise typer.Exit(code=1) from e

    console.print("\n[bold]Backup metadata:[/bold]")
    console.print(f"  Created:        {metadata.created}")
    console.print(f"  Hostname:       {metadata.hostname}")
    console.print(f"  popctl version: {metadata.popctl_version}")
