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
manifest, desktop alerts, encrypted backups, and private dotfiles. In a non-interactive shell, it
prints a static numbered guide instead.

## Workflow

### Quick Start: `popctl sync`

The `sync` command is the primary entry point. It runs the entire pipeline in a single invocation:

```bash
# Interactive (advisor prompts you for decisions)
popctl sync

# Fully automated when no new or changed source trust needs approval
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
│  1. Init          Auto-create manifest and trust sources if missing  │
│  2. Source        Refresh → preflight → preview → reconcile sources │
│  3. Diff          Compute package NEW / MISSING / EXTRA              │
│  4. Advisor       AI classifies NEW packages (keep/remove/ask)       │
│  5. Apply-M       Write advisor decisions to manifest                │
│  6. Re-Diff       Recompute diff after manifest changes              │
│  7. Confirm       Display planned package actions, ask confirmation  │
│  8. Execute       Install MISSING, remove/purge EXTRA packages       │
│  9. History       Record all actions to history                      │
│  10-14. FS        Scan → advisor → apply → cleanup → history         │
│  15-19. Configs   Scan → advisor → apply → backup+cleanup → history  │
└──────────────────────────────────────────────────────────────────────┘
```

### Package Source Reproduction

Packages can be present in a manifest while their package source is absent on a new machine. The
optional `[sources]` manifest section makes that source state reproducible:

- **APT:** enabled public source stanzas with their `Signed-By` public keys and fingerprints.
- **Flatpak:** user or system remotes with verified public keys, plus the app's remote, scope,
  architecture, and branch.
- **Snap:** each package's tracking channel.

`popctl init` and a missing-manifest `popctl sync` capture sources together with packages. Before
the manifest is saved, every replayable third-party source is shown with its identity and key
fingerprint and must be approved. A normal `popctl sync` refreshes the selected live sources before
provisioning; it asks separately about each newly discovered or changed source and saves only the
approved additions or changes. Existing extra live sources are reported but never removed.

`popctl apply`, `popctl sync`, and package-bearing `popctl backup restore` run the shared source
phase before package work. It checks selected-manager availability, platform and suite compatibility,
recorded key fingerprints, and Flatpak app/remote relationships before writing anything. Its preview
shows the source diff, public-key fingerprints, and planned commands. `--dry-run` performs the
read-only checks and preview only. `--yes` and a non-interactive invocation cannot approve a new or
changed trust relationship, so they stop rather than silently recording or replacing one.

When APT is selected, successful source reconciliation ends with the strict command
`apt-get update --error-on=any`; a source or index-refresh failure blocks later package and home
work. There is no automatic rollback of privileged source artifacts, and retained artifacts are
reported on failure.

Capture fails closed for credential-bearing or authenticated source definitions, unreadable APT
authentication stores, unsigned legacy APT entries, insecure APT options, disabled Flatpak signature
verification, and Flatpak remotes without verified public key material.

### Individual Commands

Each phase can also be run independently:

| Command | Purpose |
|---------|---------|
| `popctl init` | Create manifest and source records from current system state |
| `popctl setup` | Guided first-run setup wizard |
| `popctl scan` | Display installed packages (read-only) |
| `popctl diff` | Compare package and source records with the live system |
| `popctl apply` | Reconcile sources, then install/remove/purge packages |
| `popctl sync` | Full pipeline (init + source phase + diff + advisor + apply + orphan cleanup) |
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
| `popctl dotfiles init [--remote URL]` | Create a private dotfiles repository |
| `popctl dotfiles init --from URL` | Bootstrap dotfiles from an existing popctl repository |
| `popctl dotfiles status` | Report tracked-file and remote state without changing local files |
| `popctl dotfiles sync` | Safely synchronize and automatically push private dotfiles |
| `popctl dotfiles apply [--dry-run]` | Restore validated tracked files after packages are ready |
| `popctl manifest keep` | Move package to keep list |
| `popctl manifest remove` | Move package to remove list |
| `popctl history` | View action history |
| `popctl undo` | Revert last reversible action |
| `popctl doctor` | Check readiness of core and optional features |

### Verify Setup

```bash
popctl doctor
```

`popctl doctor` checks core package-management tools, advisor configuration and CLI availability,
desktop-alert configuration/notification/sound support, and
backup and dotfiles readiness. It exits with status 1 only when core package-management tools are
missing. Optional-feature findings are nonfatal, and advisor configuration or CLI issues are
reported as a warning because `popctl sync --no-advisor` remains available. The dotfiles check also
distinguishes an offline remote, authentication failure, timeout, and other reachability failure.

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

Initialization captures source records for available managers as well as installed packages.
Third-party source identities and public-key fingerprints are shown before they can be saved; base
distribution sources remain visible but are report-only. `popctl init --dry-run` captures and
previews without confirmation or a manifest write.

### Compare System vs Manifest

```bash
popctl diff                              # Show all differences
popctl diff --brief                      # Counts only
popctl diff --source apt                 # Filter by source
popctl diff --json                       # JSON output
```

The table, brief, and JSON forms include package drift and source drift. Source rows are `missing`,
`extra`, or `changed`; an APT package whose candidate depends on an unrecorded source is also shown
when its provenance can be resolved, or as `unknown` when it cannot.

### Apply Manifest Changes

```bash
popctl apply                             # With confirmation prompt
popctl apply --yes                       # Skip confirmation
popctl apply --source apt                # APT only
popctl apply --purge                     # Purge instead of remove (APT/Snap)
popctl apply --dry-run                   # Preview only
```

Before package actions, `apply` runs the selected source phase. A `--source` filter applies to both
source records and package actions.

### Full Sync

```bash
popctl sync                              # Interactive advisor + full pipeline
popctl sync --auto                       # Headless advisor
popctl sync --no-advisor                 # Skip all advisor phases
popctl sync --dry-run                    # Preview only
popctl sync -y -a                        # Fully automated when no new trust needs approval
popctl sync --source apt                 # Filter to APT sources and packages
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
popctl backup restore backup.tar.zst.age --source flatpak --dry-run
                                      # Preview only the backup's Flatpak sources/packages
popctl backup list                       # List local backups
popctl backup info backup.tar.zst.age    # Show backup metadata
```

A package-bearing restore loads the backup's manifest once, restores popctl state, then runs the
backup's source phase before package installation and home-file restoration. `--packages-only` uses
the same state → sources → packages order; `--files-only` remains state → home files → permissions
and does no source or package work. Restore `--source` filters both source and package work, and
restore `--dry-run` writes neither XDG state nor package or home files.

### Private Dotfiles

`popctl dotfiles` versions a reviewed, leaf-file subset of your personal configuration in a
private GitHub repository. It is deliberately not a general backup or a safe-to-publish dotfiles
tool: the repository is private by definition, and plaintext secrets are refused before they can
be offered to the advisor or committed.

```bash
# Create a new private repository, or select one interactively.
popctl dotfiles init
popctl dotfiles init --remote https://github.com/you/dotfiles.git

# Bootstrap a fresh machine from a popctl-format repository.
popctl dotfiles init --from https://github.com/you/dotfiles.git

# Inspect, synchronize, or restore the reviewed files.
popctl dotfiles status
popctl dotfiles sync
popctl dotfiles apply --dry-run
popctl dotfiles apply
```

When `gh` is installed, popctl uses it to verify that the GitHub destination is private before
initialization and immediately before every automatic push. Without `gh`, initialization requires
an explicit acknowledgement that the exact displayed URL is private. That standing acknowledgement
is bound to the canonical remote URL; changing the URL requires a fresh interactive acknowledgement
and non-interactive sync refuses. Install `gh` for per-push privacy verification.

For a fresh machine, install popctl, run the setup wizard, make the package manifest available, and
apply it. When `[sources]` is present, this preflights and restores selected sources before installing
packages; only after that package phase should you bootstrap and materialize dotfiles:

```bash
popctl setup
popctl apply
popctl dotfiles init --from https://github.com/you/dotfiles.git
popctl dotfiles apply
```

On later days, use `popctl dotfiles status` to inspect local and remote state and `popctl dotfiles
sync` to fetch, safely materialize compatible remote changes, commit reviewed local changes, and
automatically push them. A failed initial or later push leaves a valid local `pending-push` commit;
the next online sync retries it. If a tracked path changed both locally and remotely, or histories
diverge, sync refuses rather than merging into `$HOME`. Resolve that situation with plain Git using
the configured bare-repository path, for example:

```bash
git --git-dir="<bare-repo>" --work-tree="$HOME" merge origin/main
git --git-dir="<bare-repo>" --work-tree="$HOME" log --left-right main...origin/main
```

Resolve the conflict, then rerun `popctl dotfiles sync`.

### Manifest Management

```bash
popctl manifest keep vim                 # Move package to keep list
popctl manifest remove bloatware         # Move package to remove list
```

### Source Records in the Manifest

`[sources]` is optional, so older manifests continue to run without source capture, preflight, or
provisioning. When present, it is generated by `init` or the shared missing-manifest sync workflow
and is included automatically in encrypted backups. It contains public trust material, so keep the
manifest private and review any manual edits carefully.

```toml
[sources.platform]
distro_id = "ubuntu"
codename = "noble"

[[sources.apt.keys]]
id = "vendor-..."
target_path = "/etc/apt/keyrings/vendor-....asc"
armor = "-----BEGIN PGP PUBLIC KEY BLOCK-----\\n..."
fingerprints = ["FULL_UPPERCASE_FINGERPRINT"]

[[sources.apt.entries]]
id = "apt-..."
capture_path = "/etc/apt/sources.list.d/vendor.sources"
format = "deb822"                       # or "legacy"
ordinal = 0
managed_target = "popctl-apt-..."
verbatim_stanza = "Types: deb\\nURIs: https://packages.example/...\\n..."
key_ids = ["vendor-..."]
replay_mode = "replay"

[sources.apt.entries.signed_by]
key_paths = ["/etc/apt/keyrings/vendor-....asc"]
fingerprint_selectors = []

[[sources.flatpak.remotes]]
name = "flathub-beta"
scope = "user"                           # or "system"
url = "https://example.invalid/repo.flatpakrepo"
gpg_verify = true
gpg_key_armor = "-----BEGIN PGP PUBLIC KEY BLOCK-----\\n..."
gpg_fingerprints = ["FULL_UPPERCASE_FINGERPRINT"]
replay_mode = "replay"

[[sources.flatpak.apps]]
id = "org.example.App"
origin = "flathub-beta"
scope = "user"
arch = "x86_64"
branch = "beta"

[[sources.snap.packages]]
name = "example"
channel = "latest/edge"
replay_mode = "replay"
```

APT entries also retain their exact captured stanza and a durable generated target name, so a
restored source rescans as the same record. `replay_mode` is `report-only` for recognized base
distribution archives, `replay` for eligible third-party sources, or `blocked` for unsafe sources;
only `replay` records can be written on a target.

During replay, popctl installs each verified APT public key under `/etc/apt/keyrings` and rewrites
the stanza to use `signed-by=`. It imports the recorded Flatpak public key before adding the remote
in its recorded scope, then installs Flatpak apps from their recorded remote/architecture/branch and
Snaps with their recorded `--channel`. It does not use `apt-key`, does not recreate private or
credentialed sources, and never replaces the primary distribution source file.

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
| Flatpak | flatpak list | recorded remote/scope/arch/branch, or `--user` when absent | flatpak uninstall | N/A |
| Snap | snap list | recorded `--channel`, or default channel when absent | snap remove | snap remove --purge |

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

### Dotfiles Configuration

`~/.config/popctl/dotfiles.toml` is written by `popctl dotfiles init`; until then,
`popctl config edit dotfiles` prints the initialization hint instead of creating a partial config.
The normal default bare repository is `~/.local/share/popctl/dotfiles.git` (both locations follow
their XDG overrides).

```toml
bare_repo = "/home/you/.local/share/popctl/dotfiles.git"
remote_url = "https://github.com/you/dotfiles.git"
ambiguous_content_allowlist = [".config/example/settings.toml"]
ignored = [".config/example/generated-cache"]

[remote_privacy]
canonical_remote_url = "https://github.com/you/dotfiles.git"
method = "verified" # or "acknowledged" when gh was unavailable
```

`ambiguous_content_allowlist` contains only explicit path acknowledgements for ambiguous content;
it cannot allow hard secret findings. `ignored` records reviewed files that should not be proposed
again. The file has no token, password, SSH-key, or other credential field: Git and SSH use your
existing user authentication.

### File Locations

| File | Path | Purpose |
|------|------|---------|
| Manifest | `~/.config/popctl/manifest.toml` | Desired system state |
| Advisor Config | `~/.config/popctl/advisor.toml` | AI provider settings |
| Alerts Config | `~/.config/popctl/alerts.toml` | WebSocket alert sink settings |
| Theme | `~/.config/popctl/theme.toml` | Color theme overrides |
| Backup Config | `~/.config/popctl/backup.toml` | Backup encryption and target settings |
| Dotfiles Config | `~/.config/popctl/dotfiles.toml` | Dotfiles repository, remote, allowlist, and ignored paths |
| Backup Age Identity | `~/.config/age/key.txt` | Private age identity generated by `popctl backup init` |
| History | `~/.local/state/popctl/history.jsonl` | Action log for undo |
| Config Backups | `~/.local/state/popctl/config-backups/` | Backed up configs before deletion |
| Backups | `~/.local/state/popctl/backups/` | Local backup archive storage |
| Advisor Sessions | `~/.local/state/popctl/sessions/` | Workspace dirs for AI sessions (uses `~/.djinn/sessions/popctl/` only with the optional Djinn session backend) |
| Advisor Memory | `~/.local/state/popctl/advisor/memory.md` | Persistent cross-session memory |
| Dotfiles Bare Repository | `~/.local/share/popctl/dotfiles.git` | Versioned private dotfiles Git store |
| Dotfiles State | `~/.local/state/popctl/dotfiles/` | Lock, materialization plans, journals, and owned Git transport assets |

## Development

### Setup Development Environment

```bash
uv sync --dev

# Quality checks
uv run ruff check .                       # Lint
uv run pyright .                         # Type checking
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
│       ├── dotfiles.py      # popctl dotfiles {init,status,sync,apply}
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
├── dotfiles/
│   ├── config.py            # DotfilesConfig TOML I/O
│   ├── state.py             # Locks, plans, journals, and recovery
│   ├── secret_filter.py     # Fail-closed content and path checks
│   ├── discovery.py         # Bounded candidate discovery
│   ├── repo.py              # Controlled bare-repository Git operations
│   └── materialize.py       # No-clobber file materialization
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
├── sources/
│   ├── models.py            # [sources] records and replay modes
│   ├── capture.py           # Public source capture and parser boundary
│   ├── keytrust.py          # Isolated OpenPGP verification
│   ├── diff.py              # Source drift by stable locator
│   ├── preflight.py         # Compatibility, trust, and manager barrier
│   ├── provision.py         # Source reconciliation and strict APT refresh
│   └── phase.py             # Shared source workflow for init/sync/apply/restore
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
