# popctl

Declarative system configuration for Pop!_OS.

## Overview

popctl is a CLI tool that enables users to define their desired system state in a manifest file and automatically maintain that state over time. It combines deterministic package management with AI-assisted decision-making for unknown packages, orphaned filesystem directories, and stale configuration files.

## Installation

### Requirements

- Python >= 3.14
- Pop!_OS 24.04 LTS (or Debian/Ubuntu-based system with APT)

### Install with uv

```bash
# Clone the repository
git clone https://github.com/w2kr1stn/popctl.git
cd popctl

# Install with uv (recommended)
uv sync
```

## Workflow

### Quick Start: `popctl sync`

The `sync` command is the primary entry point. It runs the entire pipeline in a single invocation:

```bash
# Interactive (advisor prompts you for decisions)
popctl sync

# Fully automated (CI-friendly)
popctl sync -y -a

# Dry-run (preview only, no changes)
popctl sync --dry-run

# Skip AI advisor
popctl sync --no-advisor

# Skip filesystem/config cleanup
popctl sync --no-filesystem --no-configs
```

### Pipeline Phases

```
┌──────────────────────────────────────────────────────────────────────┐
│                         popctl sync                                  │
│                                                                      │
│  1. Init          Auto-create manifest if missing                    │
│  2. Diff          Compute NEW / MISSING / EXTRA packages             │
│  3. Advisor       AI classifies NEW packages (keep/remove/ask)       │
│  4. Apply-M       Write advisor decisions to manifest                │
│  5. Re-Diff       Recompute diff after manifest changes              │
│  6. Confirm       Display planned actions, ask confirmation          │
│  7. Execute       Install MISSING, remove/purge EXTRA packages       │
│  8. History       Record all actions to history                      │
│  9-13. FS         Scan → advisor → apply → cleanup → history         │
│  14-18. Configs   Scan → advisor → apply → backup+cleanup → history  │
└──────────────────────────────────────────────────────────────────────┘
```

### Individual Commands

Each phase can also be run independently:

| Command | Purpose |
|---------|---------|
| `popctl init` | Create manifest from current system state |
| `popctl scan` | Display installed packages (read-only) |
| `popctl diff` | Compare manifest vs. system (NEW/MISSING/EXTRA) |
| `popctl apply` | Execute install/remove/purge from manifest |
| `popctl sync` | Full pipeline (init + diff + advisor + apply + orphan cleanup) |
| `popctl advisor classify` | AI classification (headless, packages only) |
| `popctl advisor session` | AI classification (interactive, packages only) |
| `popctl advisor apply` | Apply AI decisions to manifest |
| `popctl fs scan` | Scan filesystem for orphaned directories |
| `popctl config scan` | Scan for orphaned configuration files |
| `popctl history` | View action history |
| `popctl undo` | Revert last reversible action |

## Diff Categories

When comparing manifest vs. system, popctl computes three categories:

| Category | Meaning | Action |
|----------|---------|--------|
| **NEW** | Installed but not in manifest | AI advisor decides (or user) |
| **MISSING** | In manifest (keep) but not installed | Install |
| **EXTRA** | In manifest (remove) but still installed | Remove/Purge |

## Usage

### Scan Installed Packages

```bash
popctl scan                              # All sources (APT + Flatpak + Snap)
popctl scan --source apt                 # APT only
popctl scan --manual-only                # Only manually installed
popctl scan --count                      # Summary counts only
popctl scan --export scan.json           # Export to JSON
popctl scan --format json                # JSON to stdout
```

### Initialize Manifest

```bash
popctl init                              # Create from current system
popctl init --dry-run                    # Preview without creating
popctl init --force                      # Overwrite existing
```

### Compare System vs Manifest

```bash
popctl diff                              # Show all differences
popctl diff --brief                      # Counts only
popctl diff --source apt                 # Filter by source
popctl diff --json                       # JSON output
```

### Apply Manifest Changes

```bash
popctl apply                             # With confirmation prompt
popctl apply --yes                       # Skip confirmation
popctl apply --source apt                # APT only
popctl apply --purge                     # Purge instead of remove (APT/Snap)
popctl apply --dry-run                   # Preview only
```

### Full Sync

```bash
popctl sync                              # Interactive advisor + full pipeline
popctl sync --auto                       # Headless advisor
popctl sync --no-advisor                 # Skip all advisor phases
popctl sync --dry-run                    # Preview only
popctl sync -y -a                        # Fully automated
popctl sync --source apt                 # Filter to APT packages
popctl sync --purge                      # Purge instead of remove
popctl sync --no-filesystem              # Skip filesystem orphan phases
popctl sync --no-configs                 # Skip config orphan phases
```

### Filesystem & Config Scanning

```bash
popctl fs scan                           # Scan for orphaned directories
popctl config scan                       # Scan for orphaned configs
```

### History and Undo

```bash
popctl history                           # View all history
popctl history -n 50                     # Last 50 entries
popctl history --since 2026-01-01        # Filter by date
popctl history --json                    # JSON output
popctl undo                              # Revert last reversible action
popctl undo --dry-run                    # Preview what would be undone
popctl undo --yes                        # Skip confirmation
```

### AI-Assisted Classification

```bash
popctl advisor classify                  # Headless classification
popctl advisor classify -p gemini        # Use Gemini provider
popctl advisor classify -m opus          # Use Claude Opus model
popctl advisor session                   # Interactive AI session
popctl advisor apply                     # Apply decisions to manifest
popctl advisor apply --dry-run           # Preview changes
```

## Supported Package Managers

| Manager | Scan | Install | Remove | Purge |
|---------|------|---------|--------|-------|
| APT | dpkg-query + apt-mark | apt-get install | apt-get remove | apt-get purge |
| Flatpak | flatpak list | flatpak install --user | flatpak uninstall | N/A |
| Snap | snap list | snap install | snap remove | snap remove --purge |

## Configuration

### Advisor Configuration

`~/.config/popctl/advisor.toml`:

```toml
# AI provider: "claude" or "gemini"
provider = "claude"

# Model to use (optional, defaults per provider)
model = "sonnet"

# Timeout for headless mode in seconds (default: 600)
timeout_seconds = 600

# Path to dev.sh script for container execution (optional)
# dev_script = "~/projects/ai-dev-base/scripts/dev.sh"
```

### File Locations

| File | Path | Purpose |
|------|------|---------|
| Manifest | `~/.config/popctl/manifest.toml` | Desired system state |
| Advisor Config | `~/.config/popctl/advisor.toml` | AI provider settings |
| History | `~/.local/state/popctl/history.jsonl` | Action log for undo |
| Config Backups | `~/.local/state/popctl/config-backups/` | Backed up configs before deletion |
| Advisor Sessions | `~/.local/state/popctl/advisor-sessions/` | Workspace dirs for AI sessions |
| Advisor Memory | `~/.local/state/popctl/advisor/memory.md` | Persistent cross-session memory |

## Development

### Setup Development Environment

```bash
uv sync --dev

# Quality checks
uv run fmt                               # Lint + format (Ruff)
uv run pyright app/                      # Type checking
uv run test                              # Run tests
```

### Project Structure

```
app/popctl/
├── __init__.py              # Package version
├── __main__.py              # Module entry point
├── cli/
│   ├── main.py              # Typer app, command registration
│   ├── types.py             # SourceChoice, compute_system_diff, collect_domain_orphans
│   ├── display.py           # Rich table formatting helpers
│   └── commands/
│       ├── init.py          # popctl init
│       ├── scan.py          # popctl scan
│       ├── diff.py          # popctl diff
│       ├── apply.py         # popctl apply
│       ├── sync.py          # popctl sync (main orchestrator)
│       ├── advisor.py       # popctl advisor {classify,session,apply}
│       ├── fs.py            # popctl fs {scan}
│       ├── config.py        # popctl config {scan}
│       ├── history.py       # popctl history
│       └── undo.py          # popctl undo
├── advisor/
│   ├── config.py            # AdvisorConfig, provider settings
│   ├── runner.py            # AgentRunner (headless + interactive)
│   ├── prompts.py           # AI prompt templates
│   ├── scanning.py          # scan_system() (framework-agnostic)
│   ├── workspace.py         # Session workspace creation
│   └── exchange.py          # Decision models + manifest application
├── core/
│   ├── paths.py             # XDG-compliant path helpers
│   ├── manifest.py          # Manifest TOML I/O
│   ├── baseline.py          # Protected package patterns
│   ├── diff.py              # compute_diff, diff_to_actions
│   ├── executor.py          # execute_actions, record_actions_to_history
│   ├── state.py             # History persistence (JSONL)
│   └── theme.py             # Color theme management
├── domain/
│   ├── models.py            # ScannedEntry, DomainActionResult, OrphanStatus, PathType
│   ├── ownership.py         # classify_path_type, path ownership detection
│   └── protected.py         # Protected path patterns (filesystem + configs)
├── models/
│   ├── package.py           # PackageSource, PackageStatus, ScannedPackage, ScanResult
│   ├── manifest.py          # Manifest, PackageConfig, DomainConfig (Pydantic)
│   ├── action.py            # Action, ActionResult, ActionType
│   └── history.py           # HistoryEntry, HistoryItem, HistoryActionType
├── filesystem/
│   ├── scanner.py           # FilesystemScanner (orphan detection)
│   └── operator.py          # FilesystemOperator (deletion, sudo for /etc)
├── configs/
│   ├── scanner.py           # ConfigScanner (config orphan detection)
│   └── operator.py          # ConfigOperator (backup + deletion)
├── scanners/
│   ├── base.py              # Scanner ABC
│   ├── apt.py               # AptScanner
│   ├── flatpak.py           # FlatpakScanner
│   └── snap.py              # SnapScanner
├── operators/
│   ├── base.py              # Operator ABC
│   ├── apt.py               # AptOperator (batch)
│   ├── flatpak.py           # FlatpakOperator (single-action)
│   └── snap.py              # SnapOperator (single-action)
├── utils/
│   ├── shell.py             # run_command() subprocess wrapper
│   └── formatting.py        # Rich console helpers
└── data/
    └── theme.toml           # Default color theme
```

## License

MIT
