# popctl

Declarative system configuration for Debian/Ubuntu-based (APT) systems, such as Pop!_OS.

## Overview

popctl is a CLI tool that enables users to define their desired system state in a manifest file and automatically maintain that state over time. It combines deterministic package management with AI-assisted decision-making for unknown packages, orphaned filesystem directories, and stale configuration files.

## Installation

### Requirements

- Python >= 3.14
- Debian/Ubuntu-based (APT) system, such as Pop!_OS 24.04 LTS

### Install with uv

```bash
# Clone the repository
git clone https://github.com/w2kr1stn/popctl.git
cd popctl

# Install the popctl command onto your PATH (recommended)
uv tool install .
```

For development instead, use `uv sync` (which installs into `.venv`) and prefix every
command with `uv run`, e.g. `uv run popctl setup`.

### First-Run Setup

Run the guided setup wizard as the first command after installation:

```bash
popctl setup
```

It checks core package-management tools, configures an optional AI advisor, and offers to set up a
manifest, desktop alerts, and encrypted backups. In a non-interactive shell, it prints a static
numbered guide instead.

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
| `popctl setup` | Guided first-run setup wizard |
| `popctl scan` | Display installed packages (read-only) |
| `popctl diff` | Compare manifest vs. system (NEW/MISSING/EXTRA) |
| `popctl apply` | Execute install/remove/purge from manifest |
| `popctl sync` | Full pipeline (init + diff + advisor + apply + orphan cleanup) |
| `popctl advisor classify` | AI classification (headless, packages only) |
| `popctl advisor session` | AI classification (interactive, packages only) |
| `popctl advisor apply` | Apply AI decisions to manifest |
| `popctl fs scan` | Scan filesystem for orphaned directories |
| `popctl fs clean` | Delete orphaned filesystem entries |
| `popctl config path` | List popctl configuration file locations and their status |
| `popctl config show` | Print a popctl configuration file (API keys redacted) |
| `popctl config edit` | Open a popctl configuration file in your editor |
| `popctl config scan` | Scan for orphaned configuration files |
| `popctl config clean` | Delete orphaned config files (with backup) |
| `popctl alerts init-config` | Create the editable desktop-alert configuration from the packaged template |
| `popctl alerts install-service` | Install the desktop-alert systemd user service from the packaged template |
| `popctl backup init` | Generate an age identity and matching backup configuration |
| `popctl backup create` | Create encrypted system backup |
| `popctl backup restore` | Restore from encrypted backup |
| `popctl backup list` | List available backups |
| `popctl backup info` | Show backup metadata |
| `popctl manifest keep` | Move package to keep list |
| `popctl manifest remove` | Move package to remove list |
| `popctl history` | View action history |
| `popctl undo` | Revert last reversible action |
| `popctl doctor` | Check readiness of core and optional features |

### Verify Setup

```bash
popctl doctor
```

`popctl doctor` checks core package-management tools, optional package sources, advisor
configuration and CLI availability, desktop-alert configuration/notification/sound support, and
backup dependencies. It exits with status 1 only when core package-management tools are missing.
Optional-feature findings are nonfatal, and advisor configuration or CLI issues are reported as a
warning because `popctl sync --no-advisor` remains available.

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
popctl fs clean                          # Delete orphaned directories
popctl config scan                       # Scan for orphaned configs
popctl config clean                      # Delete orphaned configs (with backup)
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

### Backup & Restore

```bash
popctl backup create                     # Create encrypted backup
popctl backup create --target remote:    # Upload to rclone remote
popctl backup restore backup.tar.zst.age # Restore from backup
popctl backup list                       # List local backups
popctl backup info backup.tar.zst.age    # Show backup metadata
```

### Manifest Management

```bash
popctl manifest keep vim                 # Move package to keep list
popctl manifest remove bloatware         # Move package to remove list
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

## Desktop Alerts

`popctl alerts` shows **desktop notifications with sound** for reminders pushed by a compatible
WebSocket alert sink (for example, a [nanobot](https://github.com/HKUDS/nanobot) instance) —
calendar events as well as standing and ad-hoc reminders. It provides a robust visual + acoustic
cue on Debian/Ubuntu-based (APT) systems, such as Pop!_OS, where desktop notification sounds from
chat apps can be unreliable. A small daemon connects to the configured alert sink, attaches to
the configured `chat_id`, and renders each alert with an explicit `expire_ms` value: an
`alerts.toml` value wins; otherwise alerts expire after 30 seconds only on a positively
identified non-COSMIC desktop and persist (`0`) on COSMIC or when the desktop is unknown;
sounds fall back through `pw-play`, `paplay`, `canberra-gtk-play`, `ffplay`, `mpv`, then `aplay` — a bundled tone is used out of the box. Structured calendar alerts get a
rich layout (title, time, location, meeting link); plain-text reminders are rendered as-is. The
These names are sent by the alert server; do not rename them: `preeve`, `pre`, and `warning`.
Their display labels are `Tomorrow`, `Soon`, and `Now`; an empty plain-text reminder uses `🔔 Reminder`.

Configure:

```bash
popctl alerts init-config
$EDITOR ~/.config/popctl/alerts.toml   # set ws_url (+ token if required)
popctl alerts test --kind warning
```

Run in the foreground, or install the systemd **user** service for an always-on setup:

```bash
popctl alerts watch                                   # Ctrl-C to stop
# or:
popctl alerts install-service
systemctl --user enable --now popctl-alerts.service
```

`popctl alerts install-service` writes the user unit with the resolved `popctl` executable path.
If user systemd is unavailable, it still writes the unit and prints the commands to run later.

## Configuration

### Advisor Configuration

`~/.config/popctl/advisor.toml`:

```toml
# AI provider: "claude", "gemini", or "codex"
provider = "claude"

# Model to use (optional, defaults per provider)
model = "sonnet"

# Optional. Leave empty when the selected provider CLI is already logged in.
api_key = ""

# Timeout for headless mode in seconds (default: 600)
timeout_seconds = 600
```

| Provider | Default model | API-key environment variable |
|----------|---------------|------------------------------|
| `claude` | `sonnet` | `ANTHROPIC_API_KEY` |
| `gemini` | `gemini-2.5-pro` | `GEMINI_API_KEY` |
| `codex` | `gpt-5.6-terra` | `OPENAI_API_KEY` |

When `api_key` is set, popctl passes it only to the selected provider CLI through the corresponding
environment variable. It is optional when that CLI is already logged in. If the selected CLI is
unavailable during an interactive advisor session, popctl points to `popctl doctor` or
`--no-advisor` rather than showing a raw command failure.

### Backup Configuration

`~/.config/popctl/backup.toml`:

```toml
# Backup target (local directory or rclone remote)
target = "remote:backups/popctl"

# age encryption recipient (public key or key file path)
recipients = "age1..."

# age identity file for decryption
identity = "~/.config/age/key.txt"

# Maximum number of backups to keep (older ones are pruned)
max_backups = 3
```

For container-based execution, install with the `agent` extra:

```bash
uv sync --extra agent
```

Run this from a checkout. It enables `djinn-in-a-box` integration, running AI sessions inside
the Djinn container.

### File Locations

| File | Path | Purpose |
|------|------|---------|
| Manifest | `~/.config/popctl/manifest.toml` | Desired system state |
| Advisor Config | `~/.config/popctl/advisor.toml` | AI provider settings |
| Alerts Config | `~/.config/popctl/alerts.toml` | WebSocket alert sink settings |
| Theme | `~/.config/popctl/theme.toml` | Color theme overrides |
| Backup Config | `~/.config/popctl/backup.toml` | Backup encryption and target settings |
| Backup Age Identity | `~/.config/age/key.txt` | Private age identity generated by `popctl backup init` |
| History | `~/.local/state/popctl/history.jsonl` | Action log for undo |
| Config Backups | `~/.local/state/popctl/config-backups/` | Backed up configs before deletion |
| Backups | `~/.local/state/popctl/backups/` | Local backup archive storage |
| Advisor Sessions | `~/.local/state/popctl/sessions/` | Workspace dirs for AI sessions (uses `~/.djinn/sessions/popctl/` only with the optional Djinn session backend) |
| Advisor Memory | `~/.local/state/popctl/advisor/memory.md` | Persistent cross-session memory |

## Development

### Setup Development Environment

```bash
uv sync --dev

# Quality checks
uv run ruff check .                       # Lint
uv run pyright app/                      # Type checking
uv run pytest                            # Run tests

# Development helpers
uv run python devops.py <fmt|test|clean>
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
│       ├── setup.py         # popctl setup
│       ├── doctor.py        # popctl doctor
│       ├── advisor.py       # popctl advisor {classify,session,apply}
│       ├── alerts.py        # popctl alerts {watch,init-config,install-service,test}
│       ├── fs.py            # popctl fs {scan,clean}
│       ├── config.py        # popctl config {path,show,edit,scan,clean}
│       ├── backup.py        # popctl backup {init,create,restore,list,info}
│       ├── manifest.py      # popctl manifest {keep,remove}
│       ├── history.py       # popctl history
│       └── undo.py          # popctl undo
├── advisor/
│   ├── _djinn_backend.py    # Lazy, typed optional djinn session backend
│   ├── config.py            # AdvisorConfig, provider settings
│   ├── runner.py            # AgentRunner (headless + interactive)
│   ├── prompts.py           # AI prompt templates
│   ├── scanning.py          # scan_system() (framework-agnostic)
│   ├── session_protocol.py  # Protocols for the optional session backend
│   ├── workspace.py         # Session workspace creation
│   └── exchange.py          # Decision models + manifest application
├── alerts/
│   ├── config.py            # AlertsConfig and TOML loading
│   ├── daemon.py            # WebSocket receive loop and reconnects
│   ├── notifier.py          # Desktop notification and sound delivery
│   ├── protocol.py          # Alert frame parsing and models
│   ├── render.py            # Notification rendering and sound selection
│   └── sounds/
│       └── alert.ogg        # Bundled default alert tone
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
│   ├── history.py           # HistoryEntry, HistoryItem, HistoryActionType
│   └── backup.py            # BackupMetadata
├── backup/
│   ├── backup.py            # Encrypted backup creation (tar|zstd|age)
│   ├── config.py            # BackupConfig
│   └── restore.py           # Backup restoration
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
    ├── theme.toml           # Default color theme
    ├── templates/
    │   ├── alerts.toml                  # Desktop-alert configuration template
    │   └── popctl-alerts.service        # Desktop-alert user-service template
    └── prompts/
        ├── initial_prompt.txt                 # Advisor initial prompt
        ├── session_claude_md.txt               # Package session instructions
        ├── session_claude_md_configs.txt       # Config session instructions
        └── session_claude_md_filesystem.txt    # Filesystem session instructions
```

## License

MIT
