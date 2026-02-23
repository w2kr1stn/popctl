 --# popctl

Declarative system configuration for Pop!_OS.

## Overview

popctl is a CLI tool that enables users to define their desired system state in a manifest file and automatically maintain that state over time. It combines deterministic package management with AI-assisted decision-making for unknown packages and configurations.

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

## Usage

### Scan Installed Packages

```bash
# Scan all sources (APT + Flatpak)
popctl scan

# Scan specific source
popctl scan --source apt
popctl scan --source flatpak

# Show only manually installed packages
popctl scan --manual-only

# Show package counts only
popctl scan --count

# Limit output to first N packages
popctl scan --limit 20

# Export to JSON file
popctl scan --export scan.json

# Output as JSON (pipe-friendly)
popctl scan --format json

# Combined options
popctl scan --source apt --manual-only --export ~/backup.json
```

### Initialize Manifest

```bash
# Create manifest from current system state
popctl init

# Preview without creating files
popctl init --dry-run

# Custom output path
popctl init --output ~/my-manifest.toml

# Overwrite existing manifest
popctl init --force
```

### Compare System vs Manifest

```bash
# Show differences between manifest and system
popctl diff

# Summary only (counts)
popctl diff --brief

# Filter by source
popctl diff --source apt

# JSON output for scripting
popctl diff --json
```

### Apply Manifest Changes

```bash
# Preview changes (dry-run, default behavior)
popctl apply --dry-run

# Apply changes with confirmation prompt
popctl apply

# Apply without confirmation
popctl apply --yes

# Apply only APT packages
popctl apply --source apt

# Use purge instead of remove for APT
popctl apply --purge
```

### History and Undo (MVP-3)

```bash
# View history of package changes
popctl history

# Limit to last N entries
popctl history -n 50

# Filter by date
popctl history --since 2026-01-01

# JSON output for scripting
popctl history --json

# Undo the last reversible action
popctl undo

# Preview what would be undone
popctl undo --dry-run

# Skip confirmation prompt
popctl undo --yes
```

### AI-Assisted Classification (MVP-2)

The advisor feature uses AI agents (Claude Code or Gemini CLI) to classify packages as keep, remove, or ask.

```bash
# Interactive mode: Prepares files, you run the AI agent manually
popctl advisor classify

# Headless mode: Runs AI classification autonomously
popctl advisor classify --auto

# Use Gemini instead of Claude
popctl advisor classify --provider gemini --auto

# Use specific model
popctl advisor classify --model opus --auto

# Apply classification decisions to manifest
popctl advisor apply

# Preview changes without modifying manifest
popctl advisor apply --dry-run
```

### Command Line Options

```
popctl --help              # Show main help
popctl --version           # Show version
popctl scan --help         # Show scan command help
popctl init --help         # Show init command help
popctl diff --help         # Show diff command help
popctl apply --help        # Show apply command help
popctl advisor --help      # Show advisor command help
popctl history --help      # Show history command help
popctl undo --help         # Show undo command help
```

## Development

### Setup Development Environment

```bash
# Install development dependencies
uv sync --dev

# Run tests
uv run pytest

# Run linter
uv run ruff check app/popctl

# Run type checker
uv run pyright app/popctl

# Run security scanner
uv run bandit -r app/popctl
```

### Project Structure

```
app/popctl/
├── __init__.py          # Package version and metadata
├── __main__.py          # Module entry point
├── cli/
│   ├── main.py          # Typer app and global options
│   └── commands/
│       ├── scan.py      # Scan command implementation
│       ├── init.py      # Init command implementation
│       ├── diff.py      # Diff command implementation
│       ├── apply.py     # Apply command implementation
│       ├── advisor.py   # Advisor command (AI classification)
│       ├── history.py   # History command (view past actions)
│       └── undo.py      # Undo command (revert last action)
├── advisor/
│   ├── config.py        # AdvisorConfig and provider settings
│   ├── runner.py        # AgentRunner for AI execution
│   ├── prompts.py       # Prompt templates for classification
│   └── exchange.py      # File exchange with AI agents
├── core/
│   ├── theme.py         # Theme management (TOML-based)
│   ├── paths.py         # XDG-compliant path helpers
│   ├── baseline.py      # Pop!_OS protected packages
│   ├── manifest.py      # Manifest TOML I/O
│   ├── diff.py          # DiffEngine for manifest comparison
│   └── state.py         # StateManager for history persistence
├── data/
│   ├── theme.toml       # Default color theme
│   └── advisor.toml     # Default advisor configuration
├── models/
│   ├── package.py       # PackageSource, PackageStatus, ScannedPackage
│   ├── scan_result.py   # ScanResult, ScanMetadata for JSON export
│   ├── manifest.py      # Manifest schema (Pydantic)
│   ├── action.py        # Action, ActionResult, ActionType
│   └── history.py       # HistoryEntry, HistoryItem, HistoryActionType
├── operators/
│   ├── base.py          # Operator ABC
│   ├── apt.py           # AptOperator implementation
│   └── flatpak.py       # FlatpakOperator implementation
├── scanners/
│   ├── base.py          # Scanner ABC
│   ├── apt.py           # AptScanner implementation
│   └── flatpak.py       # FlatpakScanner implementation
└── utils/
    ├── shell.py         # Subprocess helpers
    └── formatting.py    # Rich console formatting
```

## Roadmap

### PoC Phase (Complete ✅)
- [x] PoC-1: Hello APT - Scan APT packages
- [x] PoC-2: Multi-Source - Flatpak scanner + export
- [x] PoC-3: Manifest Birth - Generate manifest from scan
- [x] PoC-4: Diff Engine - Compare manifest vs system

### MVP Phase (Complete ✅)
- [x] MVP-1: First Apply - Install/remove packages
- [x] MVP-2: Claude Advisor - AI-assisted classification
- [x] MVP-3: Safety Net - History and undo

## License

MIT
