# popctl

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

## Workflow

The complete popctl workflow consists of six stages:

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│   init   │────>│   scan   │────>│   diff   │
│          │     │ (optional│     │          │
│ Creates  │     │  for     │     │ Compares │
│ manifest │     │  debug)  │     │ manifest │
└──────────┘     └──────────┘     │ vs system│
                                  └────┬─────┘
                                       │
                                       v
┌──────────┐     ┌──────────┐     ┌──────────┐
│  apply   │<────│ advisor  │<────│ advisor  │
│          │     │  apply   │     │ classify │
│ Executes │     │          │     │          │
│ install/ │     │ Updates  │     │ AI       │
│ remove   │     │ manifest │     │ classifies│
└──────────┘     └──────────┘     └──────────┘
```

### Stage 1: `popctl init`

**Purpose:** Creates a `manifest.toml` from the current system state.

**What happens:**

1. Checks if `manifest.toml` already exists (error unless `--force`)
2. Loads available scanners (APT, Flatpak)
3. Scans all packages from each scanner:
   - Filters: Only `PackageStatus.MANUAL` (no auto-installed dependencies)
   - Filters: Excludes protected packages (kernel, systemd, Pop!_OS core, etc.)
4. Creates manifest structure:
   - `meta`: version, created, updated timestamps
   - `system`: hostname, base distribution
   - `packages.keep`: all manually installed packages
   - `packages.remove`: empty (nothing marked for removal yet)
5. Saves to `~/.config/popctl/manifest.toml`

**Output:** `~/.config/popctl/manifest.toml`

### Stage 2: `popctl scan`

**Purpose:** Displays currently installed packages (read-only, no changes).

**What happens:**

1. Loads scanners based on `--source` (apt/flatpak/all)
2. Iterates over all available scanners, calling `scanner.scan()`
3. For each package:
   - Counts total/manual/auto
   - Collects in list
   - Optional: Filter with `--manual-only`
4. Sorts by (source, name)
5. Outputs based on flags:
   - `--format table`: Rich table (default)
   - `--format json`: JSON to stdout
   - `--export path`: JSON file
   - `--count`: Summary counts only

**Output:** Terminal table or JSON (read-only, no state changes)

### Stage 3: `popctl diff`

**Purpose:** Compares manifest with current system state.

**What happens:**

1. Loads `manifest.toml` via `require_manifest()` (error if not found)
2. Loads scanners and scans current system
3. `DiffEngine.compute_diff()` calculates three categories:

| DiffType | Symbol | Meaning | Action on `apply` |
|----------|--------|---------|-------------------|
| `NEW` | `[+]` | Installed but NOT in manifest | None (user decides) |
| `MISSING` | `[-]` | In manifest (keep) but NOT installed | INSTALL |
| `EXTRA` | `[x]` | In manifest (remove) but still installed | REMOVE |

4. Outputs table with Status, Source, Package, Note

**Output:** `DiffResult` with `new`, `missing`, `extra` tuples

### Stage 4: `popctl advisor classify`

**Purpose:** AI classifies packages into keep/remove/ask categories.

**What happens:**

1. Container warning if running in Docker/Podman
2. Loads/creates `AdvisorConfig`:
   - `provider`: "claude" or "gemini"
   - `model`: "sonnet", "opus", etc.
   - `timeout_seconds`: 600 (default)
3. Scans system (APT + Flatpak) or loads from `--input scan.json`
4. Creates exchange directory: `/tmp/popctl-exchange/`
5. Exports files:
   - `scan.json`: All packages with metadata (name, source, version, status, description, size)
   - `prompt.md`: System prompt for AI
   - `instructions.md`: Instructions for user (interactive mode)
6. Execution mode:
   - **Interactive (default):** Shows instructions for manual AI agent execution
   - **Headless (`--auto`):** AgentRunner starts AI process automatically

**AI Classification Logic:**

| Decision | Confidence | Criteria |
|----------|------------|----------|
| KEEP | >= 0.9 | System-critical, libraries, hardware support |
| REMOVE | >= 0.9 | Orphaned dependencies, obsolete, telemetry |
| ASK | < 0.9 | Uncertain, requires user decision |

**Output:** `/tmp/popctl-exchange/decisions.toml`

```toml
[apt.keep]
build-essential = { reason = "Development toolchain", confidence = 0.95 }

[apt.remove]
telemetry-pkg = { reason = "Telemetry/tracking", confidence = 0.92 }

[apt.ask]
some-tool = { reason = "Purpose unclear", confidence = 0.6 }
```

### Stage 5: `popctl advisor apply`

**Purpose:** Transfers AI decisions into the manifest.

**What happens:**

1. Loads `decisions.toml` from exchange directory (or `--input path`)
2. Loads current `manifest.toml`
3. For each source (apt, flatpak):
   - **KEEP decisions** -> `manifest.packages.keep[name]` with `PackageEntry(source, status="keep", reason=...)`
   - **REMOVE decisions** -> `manifest.packages.remove[name]` with `PackageEntry(source, status="remove", reason=...)`
   - **ASK decisions** -> Skipped (displayed for manual user decision)
4. Displays summary table (Keep/Remove/Ask counts per source)
5. Saves updated manifest (updates `meta.updated` timestamp)
6. Writes history entry (`ADVISOR_APPLY`)

**Output:** Updated `manifest.toml` with AI classifications

### Stage 6: `popctl apply`

**Purpose:** Executes the manifest (installs/removes packages).

**What happens:**

1. Loads `manifest.toml`
2. Loads scanners and computes diff
3. Converts diff to actions:
   - `MISSING` -> `Action(INSTALL, package, source)`
   - `EXTRA` -> `Action(REMOVE/PURGE, package, source)`
   - `NEW` -> **IGNORED** (user must explicitly handle)
4. Protected-check for all REMOVE actions (`is_protected()` -> action skipped)
5. Displays actions table + summary
6. Confirmation prompt (unless `--yes`)
7. Loads operators (`AptOperator`, `FlatpakOperator`)
8. Executes actions:
   - `AptOperator`: `apt-get install/remove/purge`
   - `FlatpakOperator`: `flatpak install/uninstall`
9. Displays results table (OK/FAIL per action)
10. Writes history entries (enables later `popctl undo`):
    - `INSTALL`: All installed packages
    - `REMOVE`: All removed packages
    - `PURGE`: All purged packages

**Output:** System changes + history in `~/.local/state/popctl/history.jsonl`

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

### History and Undo

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

### AI-Assisted Classification

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

## Configuration

### Advisor Configuration

The advisor can be configured via `~/.config/popctl/advisor.toml`:

```toml
# AI provider: "claude" or "gemini"
provider = "claude"

# Model to use (optional, defaults per provider)
model = "sonnet"

# Timeout for headless mode in seconds (default: 600 = 10 min)
timeout_seconds = 600

# Path to ai-dev-base dev.sh script for container execution (optional)
# dev_script = "~/projects/ai-dev-base/scripts/dev.sh"
```

### Dev Container Mode

If you're running popctl inside a development container (e.g., ai-dev-base), you need to configure the `dev_script` path so the advisor can invoke the AI agent correctly:

1. **Create/edit** `~/.config/popctl/advisor.toml`:

```toml
provider = "claude"
dev_script = "/path/to/ai-dev-base/scripts/dev.sh"
```

2. **Set the path** to your `dev.sh` script (the wrapper that launches the container with the AI CLI).

3. **Run advisor** normally:

```bash
popctl advisor classify --auto
```

The advisor will detect the `dev_script` setting and use it to invoke the AI agent through the container wrapper instead of calling `claude` or `gemini` directly.

**Note:** When running inside a container, popctl displays a warning because package scanning and system modifications may not work correctly. For best results, run popctl directly on the host system.

### File Locations

| File | Path | Purpose |
|------|------|---------|
| Manifest | `~/.config/popctl/manifest.toml` | Desired system state |
| Advisor Config | `~/.config/popctl/advisor.toml` | AI provider settings |
| History | `~/.local/state/popctl/history.jsonl` | Action log for undo |
| Exchange Dir | `/tmp/popctl-exchange/` | AI agent communication |

## Development

### Setup Development Environment

```bash
# Install development dependencies
uv sync --dev

# Run all quality checks (lint + format)
uv run fmt

# Run type checker
uv run pyright .

# Run tests
uv run test

# Run tests with coverage report
uv run testcov

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
│   ├── types.py         # Shared CLI types (SourceChoice, scanner helpers)
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

## License

MIT
