# IMPLEMENTATION.md — popctl Deep Dive

This document provides a comprehensive technical reference for the popctl implementation, covering architecture, data flow, module responsibilities, and design decisions.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Layer Breakdown](#layer-breakdown)
3. [Data Models](#data-models)
4. [Core Modules](#core-modules)
5. [Package Scanners & Operators](#package-scanners--operators)
6. [Domain Layer (Filesystem & Configs)](#domain-layer-filesystem--configs)
7. [AI Advisor System](#ai-advisor-system)
8. [The Sync Pipeline](#the-sync-pipeline)
9. [CLI Commands](#cli-commands)
10. [Data Flow Examples](#data-flow-examples)
11. [Design Decisions](#design-decisions)

---

## Architecture Overview

popctl follows a **Modular Monolith** architecture with strict layer separation across 7 layers:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CLI Layer (commands/)                         │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐   │
│  │ init │ │ scan │ │ diff │ │apply │ │ sync │ │  fs  │ │config│   │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘   │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐                      │
│  │undo  │ │hist. │ │advis.│ │backup│ │manif.│                      │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘                      │
├─────────────────────────────────────────────────────────────────────┤
│                   CLI Support (types.py, display.py)                 │
│  SourceChoice, compute_system_diff, collect_domain_orphans,         │
│  create_actions_table, print_orphan_table                           │
├─────────────────────────────────────────────────────────────────────┤
│                   Core Orchestration                                 │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                │
│  │  executor.py  │ │   diff.py    │ │  state.py    │                │
│  │ execute_      │ │ compute_diff │ │ record_      │                │
│  │ actions()     │ │ diff_to_     │ │ action()     │                │
│  └──────────────┘ │ actions()    │ │ get_history()│                │
│                    └──────────────┘ └──────────────┘                │
├─────────────────────────────────────────────────────────────────────┤
│                   Core I/O                                           │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                │
│  │ manifest.py   │ │  paths.py    │ │ baseline.py  │                │
│  │ load/save     │ │ XDG paths    │ │ protected    │                │
│  │ manifest      │ │ ensure_dir   │ │ packages     │                │
│  └──────────────┘ └──────────────┘ └──────────────┘                │
├─────────────────────────────────────────────────────────────────────┤
│                   Domain Layer                                       │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                │
│  │  models.py    │ │ownership.py  │ │ protected.py │                │
│  │ ScannedEntry  │ │classify_     │ │ is_protected │                │
│  │ DomainAction  │ │path_type()   │ │ (filesystem  │                │
│  │ Result        │ │              │ │  + configs)  │                │
│  └──────────────┘ └──────────────┘ └──────────────┘                │
├─────────────────────────────────────────────────────────────────────┤
│                   Models Layer                                       │
│  ┌─────────┐ ┌───────────┐ ┌──────────┐ ┌───────────┐              │
│  │package  │ │ manifest  │ │ action   │ │ history   │              │
│  │.py      │ │ .py       │ │ .py      │ │ .py       │              │
│  └─────────┘ └───────────┘ └──────────┘ └───────────┘              │
├─────────────────────────────────────────────────────────────────────┤
│          Scanners/Operators/Advisor/Filesystem/Configs/Backup        │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────┐                 │
│  │  scanners/   │ │  operators/  │ │   advisor/   │                 │
│  │ APT,Flatpak, │ │ APT,Flatpak, │ │ config,      │                 │
│  │ Snap         │ │ Snap         │ │ runner,      │                 │
│  └─────────────┘ └──────────────┘ │ workspace,   │                 │
│  ┌─────────────┐ ┌──────────────┐ │ exchange     │                 │
│  │ filesystem/  │ │   configs/   │ └──────────────┘                 │
│  │ scanner,     │ │ scanner,     │ ┌──────────────┐                 │
│  │ operator     │ │ operator     │ │   backup/    │                 │
│  └─────────────┘ └──────────────┘ │ backup,      │                 │
│                                    │ config,      │                 │
│                                    │ restore      │                 │
│                                    └──────────────┘                 │
├─────────────────────────────────────────────────────────────────────┤
│                   Utils (shell.py, formatting.py)                    │
└─────────────────────────────────────────────────────────────────────┘
```

**Key principles:**
- **Immutable data models** — All domain models use `@dataclass(frozen=True, slots=True)`
- **No direct subprocess calls** — All shell commands go through `utils/shell.py`
- **XDG compliance** — All paths use `core/paths.py` helpers
- **Pydantic for boundaries** — Config/manifest use Pydantic for TOML validation
- **Dataclasses for domain** — Internal models use frozen dataclasses for performance

---

## Layer Breakdown

### CLI Layer (`cli/commands/`)

| File | Commands |
|------|----------|
| `init.py` | `popctl init` |
| `scan.py` | `popctl scan` |
| `diff.py` | `popctl diff` |
| `apply.py` | `popctl apply` |
| `sync.py` | `popctl sync` (main orchestrator) |
| `advisor.py` | `popctl advisor {classify,session,apply}` |
| `fs.py` | `popctl fs {scan,clean}` |
| `config.py` | `popctl config {scan,clean}` |
| `backup.py` | `popctl backup {create,restore,list,info}` |
| `manifest.py` | `popctl manifest {keep,remove}` |
| `history.py` | `popctl history` |
| `undo.py` | `popctl undo` |

**Pattern:** Commands are thin wrappers that parse arguments, call core/domain functions, and format output with Rich console.

### CLI Support (`cli/types.py`, `cli/display.py`)

| File | Responsibility |
|------|----------------|
| `types.py` | `SourceChoice` enum, `compute_system_diff()`, `collect_domain_orphans()`, `require_manifest()`, `get_checked_scanners()` |
| `display.py` | `create_actions_table()`, `create_results_table()`, `print_orphan_table()`, `print_actions_summary()`, `print_results_summary()` |

### Core Orchestration (`core/`)

| File | Responsibility |
|------|----------------|
| `executor.py` | `execute_actions()` — dispatches actions to operators; `record_actions_to_history()` — records results |
| `diff.py` | `compute_diff()` — manifest vs. system comparison; `diff_to_actions()` — converts diff to Action list |
| `state.py` | `record_action()`, `get_history()`, `get_last_reversible()`, `mark_entry_reversed()`, `record_domain_deletions()` |

### Core I/O (`core/`)

| File | Responsibility |
|------|----------------|
| `paths.py` | XDG path resolution (`get_config_dir()`, `get_state_dir()`, `get_manifest_path()`, `ensure_dir()`) |
| `manifest.py` | `load_manifest()`, `save_manifest()`, `manifest_exists()`, `scan_and_create_manifest()` |
| `baseline.py` | `PROTECTED_PACKAGE_PATTERNS`, `PROTECTED_PACKAGES`, `is_package_protected()` |
| `theme.py` | Color theme loading from TOML |

### Domain Layer (`domain/`)

| File | Responsibility |
|------|----------------|
| `models.py` | `ScannedEntry`, `DomainActionResult`, `OrphanStatus`, `PathType`, `OrphanReason` |
| `ownership.py` | `classify_path_type()` — shared by FilesystemScanner and ConfigScanner |
| `protected.py` | `is_protected(path, domain)` — protected path patterns for filesystem and configs |

### Backup (`backup/`)

| File | Responsibility |
|------|----------------|
| `backup.py` | `create_backup()` — streams tar|zstd|age pipeline, `collect_backup_files()`, auto-prune with `max_backups` retention |
| `config.py` | `load_backup_config()` — reads `~/.config/popctl/backup.toml` (target, recipients, identity, max_backups) |
| `restore.py` | `restore_backup()` — decrypt, decompress, restore files + packages; `read_backup_metadata()`, `list_backups()` |

### Infrastructure (`utils/`)

| File | Responsibility |
|------|----------------|
| `shell.py` | `run_command()` subprocess wrapper with timeout, `CommandResult` |
| `formatting.py` | Rich console helpers (`print_info`, `print_error`, `print_warning`, `print_success`, `console`) |

---

## Data Models

### Package Models (`models/package.py`)

```python
class PackageSource(Enum):
    APT = "apt"
    FLATPAK = "flatpak"
    SNAP = "snap"

class PackageStatus(Enum):
    MANUAL = "manual"
    AUTO_INSTALLED = "auto"

@dataclass(frozen=True, slots=True)
class ScannedPackage:
    name: str
    source: PackageSource
    version: str
    status: PackageStatus
    description: str | None = None
    size_bytes: int | None = None

# Tuple alias for scan results
ScanResult = tuple[ScannedPackage, ...]
```

### Action Models (`models/action.py`)

```python
class ActionType(Enum):
    INSTALL = "install"
    REMOVE = "remove"
    PURGE = "purge"

@dataclass(frozen=True, slots=True)
class Action:
    action_type: ActionType
    package: str
    source: PackageSource
    # Validates: PURGE only for APT and SNAP

@dataclass(frozen=True, slots=True)
class ActionResult:
    action: Action
    success: bool
    detail: str | None = None

    @property
    def failed(self) -> bool: ...
```

### History Models (`models/history.py`)

```python
class HistoryActionType(Enum):
    INSTALL = "install"
    REMOVE = "remove"
    PURGE = "purge"
    ADVISOR_APPLY = "advisor_apply"
    FS_DELETE = "fs_delete"
    CONFIG_DELETE = "config_delete"

@dataclass(frozen=True, slots=True)
class HistoryItem:
    name: str
    source: PackageSource | None = None  # None for domain deletions

@dataclass(frozen=True, slots=True)
class HistoryEntry:
    id: str                          # 12-char UUID hex prefix
    timestamp: str                   # ISO 8601 with timezone
    action_type: HistoryActionType
    items: tuple[HistoryItem, ...]   # Immutable tuple
    reversible: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
```

**JSONL serialization:** `to_json_line()` / `from_json_line()` for append-only storage.

**Factory function:** `create_history_entry()` auto-generates ID (UUID hex[:12]) and timestamp.

### Manifest Models (`models/manifest.py`)

Pydantic models with strict validation:

```python
PackageSourceType = Literal["apt", "flatpak", "snap"]

class PackageEntry(BaseModel):
    source: PackageSourceType
    reason: str | None = None

class PackageConfig(BaseModel):
    keep: dict[str, PackageEntry]
    remove: dict[str, PackageEntry]
    # Cross-field validation: no key in both keep and remove

class DomainEntry(BaseModel):
    reason: str | None = None
    category: str | None = None

class DomainConfig(BaseModel):
    keep: dict[str, DomainEntry]
    remove: dict[str, DomainEntry]
    # Cross-field validation: no key in both keep and remove

class Manifest(BaseModel):
    meta: ManifestMeta
    system: SystemConfig
    packages: PackageConfig
    filesystem: DomainConfig | None = None
    configs: DomainConfig | None = None
```

### Domain Models (`domain/models.py`)

```python
class OrphanStatus(Enum):
    ORPHAN = "orphan"
    OWNED = "owned"
    PROTECTED = "protected"

class PathType(Enum):
    DIRECTORY = "directory"
    FILE = "file"
    SYMLINK = "symlink"
    DEAD_SYMLINK = "dead_symlink"

@dataclass(frozen=True, slots=True)
class ScannedEntry:
    path: str
    path_type: PathType
    status: OrphanStatus
    size_bytes: int | None
    mtime: str | None
    parent_target: str | None       # Filesystem only
    orphan_reason: OrphanReason | None
    confidence: float               # 0.0 to 1.0

@dataclass(frozen=True, slots=True)
class DomainActionResult:
    path: str
    success: bool
    error: str | None = None
    dry_run: bool = False
    backup_path: str | None = None
```

---

## Core Modules

### paths.py — XDG Path Management

```python
get_config_dir()    # ~/.config/popctl/
get_state_dir()     # ~/.local/state/popctl/
get_manifest_path() # ~/.config/popctl/manifest.toml
ensure_dir(path, name) -> Path  # mkdir -p with error handling
```

### baseline.py — Protected Package Management

Defines system-critical packages that must never be removed:

- **Pattern matching:** `linux-*`, `systemd*`, `cosmic-*`, `pop-*`, `apt*`, `dpkg*`, etc.
- **Exact matches:** `bash`, `coreutils`, `sudo`, `snapd`, etc.
- **Used by:** `compute_diff()` (filters from diff), `apply` and `undo` (blocks removal)

### diff.py — Diff Engine

```python
class DiffType(Enum):
    NEW = "new"         # Installed but not in manifest
    MISSING = "missing" # In manifest but not installed
    EXTRA = "extra"     # Marked for removal but still installed

@dataclass(frozen=True, slots=True)
class DiffEntry:
    name: str
    source: PackageSource
    diff_type: DiffType
    version: str | None = None
    description: str | None = None

@dataclass(frozen=True, slots=True)
class DiffResult:
    new: tuple[DiffEntry, ...]
    missing: tuple[DiffEntry, ...]
    extra: tuple[DiffEntry, ...]

    @property
    def is_in_sync(self) -> bool: ...

def compute_diff(manifest, scanners, source_filter=None) -> DiffResult
def diff_to_actions(diff_result, purge=False) -> list[Action]
```

**Diff logic:**
- Only `MANUAL` packages considered (auto-installed dependencies ignored)
- Protected packages filtered via `is_package_protected()`
- NEW = `installed ∉ keep ∧ installed ∉ remove`
- MISSING = `keep ∉ installed`
- EXTRA = `remove ∈ installed`

**Action conversion:**
- MISSING → `INSTALL`
- EXTRA → `REMOVE` (or `PURGE` if `--purge` and APT/Snap)
- NEW → no action (handled by advisor or user)

### executor.py — Action Execution

```python
def execute_actions(actions: list[Action], operators: list[Operator]) -> list[ActionResult]
def record_actions_to_history(results: list[ActionResult], command: str) -> None
```

**Execution flow:**
1. Group actions by `source` (`defaultdict(list)`)
2. For each operator, split into `install_pkgs`, `remove_pkgs`, `purge_pkgs`
3. Call `operator.install()`, `operator.remove(purge=False)`, `operator.remove(purge=True)`

**History mapping:** Uses `_PACKAGE_TO_HISTORY` dict to convert `ActionType` → `HistoryActionType` (no runtime string conversion).

### state.py — History Persistence

```python
HISTORY_FILENAME = "history.jsonl"

INVERSE_ACTION_TYPES: dict[HistoryActionType, HistoryActionType] = {
    INSTALL: REMOVE, REMOVE: INSTALL, PURGE: INSTALL
}

def record_action(entry, state_dir=None) -> None      # Append to JSONL
def get_history(limit=None, since=None) -> tuple[list[HistoryEntry], int]  # Newest first; second element is corrupt line count
def get_last_reversible() -> HistoryEntry | None       # For undo
def mark_entry_reversed(entry) -> None                 # Append reversal marker
def record_domain_deletions(domain, paths, command) -> None  # FS/config history
```

**Reversal tracking:** Append-only — reversal marker entries reference the original entry ID. `get_last_reversible()` builds a set of reversed IDs and skips them.

---

## Package Scanners & Operators

### Scanner ABC (`scanners/base.py`)

```python
class Scanner(ABC):
    source: PackageSource

    @abstractmethod
    def scan(self) -> Iterator[ScannedPackage]: ...

    @abstractmethod
    def is_available(self) -> bool: ...
```

| Scanner | Command | Status Logic |
|---------|---------|--------------|
| `AptScanner` | `dpkg-query -W` + `apt-mark showauto` | MANUAL if not in auto set |
| `FlatpakScanner` | `flatpak list --app --columns=...` | All apps treated as MANUAL |
| `SnapScanner` | `snap list` | All snaps treated as MANUAL |

### Operator ABC (`operators/base.py`)

```python
class Operator(ABC):
    source: PackageSource

    @abstractmethod
    def install(self, packages: list[str]) -> list[ActionResult]: ...

    @abstractmethod
    def remove(self, packages: list[str], purge: bool = False) -> list[ActionResult]: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    def _run_single(self, action, args) -> ActionResult    # For single-action operators
    def _dry_run_result(self, action) -> ActionResult
    def _create_result(self, action, result) -> ActionResult
```

**Two execution strategies:**
- **Batch** (APT): All packages in one `apt-get` command. APT's transactional semantics make this correct.
- **Single-action** (Flatpak, Snap): One command per package via `_run_single()` for error isolation.

| Operator | Install | Remove | Purge |
|----------|---------|--------|-------|
| `AptOperator` | `sudo apt-get install -y -- <pkgs>` | `sudo apt-get remove -y -- <pkgs>` | `sudo apt-get purge -y -- <pkgs>` |
| `FlatpakOperator` | `flatpak install -y --user -- <pkg>` | `flatpak uninstall -y -- <pkg>` | N/A |
| `SnapOperator` | `sudo snap install -- <pkg>` | `sudo snap remove -- <pkg>` | `sudo snap remove --purge -- <pkg>` |

---

## Domain Layer (Filesystem & Configs)

The domain layer handles orphaned filesystem directories and configuration files — paths that no longer belong to any installed package.

### Domain Scanners

| Scanner | Module | Scan Targets |
|---------|--------|--------------|
| `FilesystemScanner` | `filesystem/scanner.py` | `~/.config/`, `~/.local/share/`, `~/.cache/`, optionally `/etc/` |
| `ConfigScanner` | `configs/scanner.py` | Config-specific paths under `~/.config/` |

Both scanners:
1. Use `classify_path_type()` from `domain/ownership.py` for path type detection
2. Check ownership via `dpkg -S`, flatpak/snap app lists, `.desktop` file matching
3. Return `Iterator[ScannedEntry]` with `OrphanStatus` (ORPHAN/OWNED/PROTECTED) and confidence score

### Domain Operators

| Operator | Module | Behavior |
|----------|--------|----------|
| `FilesystemOperator` | `filesystem/operator.py` | Direct deletion. `/etc/` paths use `sudo rm -rf`. Dirs use `shutil.rmtree()`. Files use `Path.unlink()` |
| `ConfigOperator` | `configs/operator.py` | **Backup before delete.** Creates timestamped backup under `~/.local/state/popctl/config-backups/<timestamp>/` preserving relative directory structure. Then deletes. |

### Protected Paths (`domain/protected.py`)

```python
is_protected(path: str, domain: Literal["filesystem", "configs"]) -> bool
```

Per-domain patterns protect critical paths:
- **Common:** `~/.ssh/*`, `~/.gnupg/*`, `~/.config/cosmic*`, `~/.config/popctl`
- **Filesystem:** `/etc/fstab`, `/etc/passwd`, `~/.local/share/flatpak`, etc.
- **Configs:** `~/.bashrc`, `~/.zshrc`, `~/.config/flatpak`, etc.

Protected paths are checked at **two** levels (defense-in-depth):
1. CLI level — user-visible "Skipping protected" warnings
2. Operator level — hard rejection with error result

---

## AI Advisor System

### Architecture

The advisor uses a **workspace-based session protocol**:

```
┌──────────────┐     ┌─────────────────────────────┐     ┌──────────────┐
│   popctl     │────▶│  Session Workspace           │────▶│  AI Agent    │
│              │     │  ~/.djinn/sessions/popctl/    │     │ (Claude/     │
│  (sync or   │     │  <timestamp>/                 │     │  Gemini)     │
│   advisor)  │◀────│    CLAUDE.md                  │◀────│              │
│              │     │    scan.json                  │     │              │
│              │     │    manifest.toml              │     │              │
│              │     │    memory.md                  │     │              │
│              │     │    output/decisions.toml      │     │              │
└──────────────┘     └─────────────────────────────┘     └──────────────┘
```

When `djinn-in-a-box` is installed (optional dependency), `AgentRunner` delegates to
`SessionManager("popctl")` which can run the AI agent inside the Djinn container via
`docker exec`. The shared host directory `~/.djinn/sessions/` is bind-mounted into the
container, so workspaces are accessible from both host and container. Without djinn,
the agent runs directly on the host.

### Workspace Structure

Each advisor invocation creates an ephemeral session directory:

```
~/.djinn/sessions/popctl/20260223T143000/
├── CLAUDE.md           # Agent instructions (auto-picked up by Claude Code)
├── scan.json           # Package scan data + optional orphan entries
├── manifest.toml       # Current manifest copy
├── memory.md           # Cross-session learning (copied from persistent or previous session)
└── output/
    └── decisions.toml  # Written by the AI agent
```

### Decision Models (`advisor/exchange.py`)

```python
class PackageDecision(BaseModel):   # name, reason, confidence, category
class SourceDecisions(BaseModel):   # keep, remove, ask: list[PackageDecision]
class PathDecision(BaseModel):      # path, reason, confidence, category
class DomainDecisions(BaseModel):   # keep, remove, ask: list[PathDecision]

class DecisionsResult(BaseModel):
    packages: dict[PackageSourceType, SourceDecisions]
    filesystem: DomainDecisions | None = None
    configs: DomainDecisions | None = None
```

### Key Functions

```python
# Import and validate decisions.toml
import_decisions(path) -> DecisionsResult

# Apply package decisions to manifest (mutates manifest.packages)
apply_decisions_to_manifest(manifest, decisions) -> (stats, ask_packages)

# Apply domain decisions to manifest (mutates manifest.filesystem/configs)
apply_domain_decisions_to_manifest(manifest, domain, decisions) -> list[PathDecision]

# Record advisor apply to history
record_advisor_apply_to_history(decisions) -> None
```

### Execution Modes

| Mode | Function | Usage |
|------|----------|-------|
| Headless | `AgentRunner.run_headless()` | `popctl sync --auto`, `popctl advisor classify` |
| Interactive | `AgentRunner.launch_interactive()` | `popctl sync` (default), `popctl advisor session` |

Both modes delegate to `SessionManager` when available (container execution), falling back to
direct host CLI execution otherwise.

### Cross-Session Memory

The advisor maintains persistent memory at `~/.local/state/popctl/advisor/memory.md`. When creating a workspace:
1. Try persistent `memory.md` first
2. Fallback: copy from most recent previous session (chaining for host-mode)
3. Warn if memory exceeds 50 KB

---

## The Sync Pipeline

The `sync` command (`cli/commands/sync.py`) is the main orchestrator. It runs up to 18 phases:

```
sync()
├── Phase 1:  _ensure_manifest()          — auto-init if missing
├── _sync_packages()
│   ├── Phase 2:  compute_system_diff()   — NEW/MISSING/EXTRA
│   ├── Phase 3:  _run_advisor()          — AI classifies NEW packages
│   ├── Phase 4:  _apply_advisor_decisions() — write to manifest
│   ├── Phase 5:  compute_system_diff()   — re-diff after changes
│   ├── Phase 6:  diff_to_actions()       — convert + user confirm
│   ├── Phase 7:  execute_actions()       — install/remove/purge
│   └── Phase 8:  record_actions_to_history()
├── _run_orphan_phases("filesystem")
│   ├── Phase 9:  _domain_scan()          — FilesystemScanner
│   ├── Phase 10: _domain_run_advisor()   — AI classifies orphans
│   ├── Phase 11: _domain_apply_decisions() — write to manifest
│   ├── Phase 12: _domain_clean()         — delete paths
│   └── Phase 13: _record_orphan_history()
└── _run_orphan_phases("configs")
    ├── Phase 14: _domain_scan()          — ConfigScanner
    ├── Phase 15: _domain_run_advisor()   — AI classifies orphans
    ├── Phase 16: _domain_apply_decisions() — write to manifest
    ├── Phase 17: _domain_clean()         — backup + delete
    └── Phase 18: _record_orphan_history()
```

### Error Handling Philosophy

| Phase | Severity | Behavior |
|-------|----------|----------|
| Init (1) | **Fatal** | No manifest → `Exit(1)` |
| Diff (2) | **Fatal** | Scanner/manifest error → `Exit(1)` |
| Advisor (3-5) | **Non-fatal** | Warning printed, sync continues with existing manifest |
| Confirm (6) | **User abort** | `Exit(0)`, propagates past orphan phases |
| Execute (7) | **Per-action** | Failed actions collected, successful ones recorded |
| History (8) | **Non-fatal** | Warning on write failure |
| Scan (9,14) | **Non-fatal** | Scan failure returns `None` (skip remaining phases with warning); clean system returns `[]` (no orphans, skip silently) |
| Orphans (10-18) | **Non-fatal** | Each sub-phase catches own errors |

### CLI Flags

| Flag | Effect |
|------|--------|
| `--yes` / `-y` | Skip all confirmation prompts |
| `--dry-run` / `-n` | Show diff/scan results only, no changes |
| `--source` / `-s` | Filter to APT/Flatpak/Snap/All |
| `--purge` / `-p` | Use purge instead of remove (APT/Snap) |
| `--no-advisor` | Skip all AI advisor phases |
| `--auto` / `-a` | Use headless advisor (no interaction) |
| `--no-filesystem` | Skip filesystem orphan phases (9-13) |
| `--no-configs` | Skip config orphan phases (14-18) |

---

## CLI Commands

### sync (`cli/commands/sync.py`)

The main orchestrator. See [The Sync Pipeline](#the-sync-pipeline) for details.

### init (`cli/commands/init.py`)

| Option | Description |
|--------|-------------|
| `--output PATH` | Custom output path |
| `--dry-run` | Preview without creating |
| `--force` | Overwrite existing manifest |

Scans all available package managers, filters to manual packages, excludes protected packages, creates and saves manifest.

### scan (`cli/commands/scan.py`)

| Option | Description |
|--------|-------------|
| `--source` | Filter by source (apt/flatpak/snap/all) |
| `--manual-only` | Only show manually installed packages |
| `--count` | Show package counts only |
| `--limit N` | Limit output to N packages |
| `--export FILE` | Export to JSON file |
| `--format json` | Output as JSON to stdout |

### diff (`cli/commands/diff.py`)

| Option | Description |
|--------|-------------|
| `--source` | Filter by source |
| `--brief` | Show counts only |
| `--json` | JSON output |

### apply (`cli/commands/apply.py`)

| Option | Description |
|--------|-------------|
| `--yes` | Skip confirmation |
| `--source` | Apply only specific source |
| `--purge` | Use purge instead of remove |
| `--dry-run` | Preview only |

### advisor (`cli/commands/advisor.py`)

**`popctl advisor classify`** — Headless AI classification (packages only).

**`popctl advisor session`** — Interactive AI session (packages only).

**`popctl advisor apply`** — Apply decisions from latest session to manifest.

Note: Domain orphan advising (filesystem/configs) is only available through `popctl sync`.

### fs / config (`cli/commands/fs.py`, `cli/commands/config.py`)

**`popctl fs scan`** — Scan filesystem for orphaned directories.

**`popctl fs clean`** — Delete orphaned filesystem paths (with confirmation).

**`popctl config scan`** — Scan for orphaned configuration files.

**`popctl config clean`** — Delete orphaned config files (with backup + confirmation).

### backup (`cli/commands/backup.py`)

**`popctl backup create`** — Create an encrypted backup (tar|zstd|age pipeline). Supports local paths and rclone remotes. Auto-prunes old backups based on `max_backups` config.

**`popctl backup restore <source>`** — Restore from an encrypted backup. Decrypts, decompresses, restores files and/or installs packages. Supports `--files-only` and `--packages-only` modes.

**`popctl backup list`** — List available backups at a target location.

**`popctl backup info <source>`** — Show backup metadata without restoring.

### manifest (`cli/commands/manifest.py`)

**`popctl manifest keep <name>`** — Add a package to the manifest keep list.

**`popctl manifest remove <name>`** — Add a package to the manifest remove list.

### history (`cli/commands/history.py`)

| Option | Description |
|--------|-------------|
| `-n N` | Limit to N entries |
| `--since DATE` | Filter by date (YYYY-MM-DD) |
| `--json` | JSON output |

### undo (`cli/commands/undo.py`)

| Option | Description |
|--------|-------------|
| `--dry-run` | Preview without executing |
| `--yes` | Skip confirmation |

Finds last reversible entry, computes inverse actions (INSTALL ↔ REMOVE), checks protected packages, executes, marks entry as reversed.

---

## Data Flow Examples

### Example 1: `popctl sync --auto --yes`

```
sync()
  │
  ├─ _ensure_manifest()
  │    └─ manifest_exists() → True (skip)
  │
  ├─ _sync_packages()
  │    ├─ compute_system_diff(ALL)
  │    │    ├─ require_manifest() → Manifest
  │    │    ├─ get_checked_scanners() → [AptScanner, FlatpakScanner, SnapScanner]
  │    │    └─ compute_diff() → DiffResult(new=5, missing=2, extra=1)
  │    │
  │    ├─ _run_advisor(diff_result, auto=True)
  │    │    └─ _invoke_advisor(auto=True, domain="packages")
  │    │         ├─ load_or_create_config() → AdvisorConfig
  │    │         ├─ scan_system() → ScanResult
  │    │         ├─ create_session_workspace() → /path/to/session/
  │    │         ├─ runner.run_headless() → AgentResult
  │    │         └─ import_decisions() → DecisionsResult
  │    │
  │    ├─ _apply_advisor_decisions(decisions)
  │    │    ├─ load_manifest() → Manifest
  │    │    ├─ apply_decisions_to_manifest(manifest, decisions)
  │    │    ├─ save_manifest(manifest)
  │    │    └─ record_advisor_apply_to_history(decisions)
  │    │
  │    ├─ compute_system_diff(ALL)  (re-diff)
  │    │    └─ DiffResult(new=0, missing=2, extra=3)
  │    │
  │    ├─ diff_to_actions(purge=False) → [INSTALL×2, REMOVE×3]
  │    │
  │    ├─ execute_actions(actions, operators)
  │    │    ├─ AptOperator.install(["pkg1", "pkg2"])
  │    │    └─ AptOperator.remove(["pkg3", "pkg4", "pkg5"])
  │    │
  │    └─ record_actions_to_history(results)
  │
  ├─ _run_orphan_phases("filesystem")
  │    ├─ collect_domain_orphans("filesystem") → [ScannedEntry×12]
  │    ├─ _domain_run_advisor("filesystem", orphans, auto=True)
  │    ├─ _domain_apply_decisions("filesystem", decisions)
  │    ├─ _domain_clean("filesystem", yes=True)
  │    │    ├─ FilesystemOperator().delete(paths)
  │    │    └─ → deleted_paths
  │    └─ record_domain_deletions("filesystem", deleted_paths)
  │
  └─ _run_orphan_phases("configs")
       ├─ collect_domain_orphans("configs") → [ScannedEntry×3]
       ├─ _domain_run_advisor("configs", orphans, auto=True)
       ├─ _domain_apply_decisions("configs", decisions)
       ├─ _domain_clean("configs", yes=True)
       │    ├─ ConfigOperator().delete(paths)
       │    │    ├─ _backup_path() → ~/.local/state/popctl/config-backups/...
       │    │    └─ shutil.rmtree() / Path.unlink()
       │    └─ → deleted_paths
       └─ record_domain_deletions("configs", deleted_paths)
```

### Example 2: `popctl undo`

```
undo.py
  │
  ├─ get_last_reversible() → HistoryEntry(INSTALL, items=[vim, htop])
  │
  ├─ Compute inverse: INSTALL → REMOVE
  │    └─ INVERSE_ACTION_TYPES[INSTALL] = REMOVE
  │
  ├─ Check protected: is_package_protected("vim") → False ✓
  │
  ├─ Show preview, typer.confirm()
  │
  ├─ execute_actions([Action(REMOVE, "vim"), Action(REMOVE, "htop")], operators)
  │
  └─ mark_entry_reversed(entry)
       └─ Appends reversal marker to history.jsonl
```

---

## Design Decisions

### Why Frozen Dataclasses for Domain Models?

1. **Immutability** — Once created, objects cannot be accidentally modified
2. **Hashable** — Can be used in sets and as dict keys
3. **Thread-safe** — No synchronization needed
4. **Explicit mutations** — Must create new objects, making changes visible

### Why Pydantic for Manifest but Dataclasses for Domain?

1. **Manifest** — External data (TOML) needs validation, schema enforcement, `extra="forbid"`
2. **Domain models** — Internal data, already validated at boundaries
3. **Performance** — Dataclasses are faster for frequent object creation

### Why JSONL for History?

1. **Append-only** — No need to rewrite entire file
2. **Corruption-resistant** — Corrupt line only affects that entry
3. **Streamable** — Process large files line-by-line
4. **Reversal tracking** — Append reversal markers instead of mutating entries

### Why Workspace-Based Advisor (not Exchange Directory)?

1. **Self-contained** — Each session has all context in one directory
2. **CLAUDE.md auto-pickup** — Claude Code automatically reads agent instructions
3. **Memory chaining** — Cross-session learning via memory.md
4. **Audit trail** — Session directories preserved for debugging

### Why Separate Scanners for Packages vs. Domain?

- **Package scanners** (`scanners/`): Query package managers (APT, Flatpak, Snap) for installed packages
- **Domain scanners** (`filesystem/`, `configs/`): Walk filesystem, check ownership via dpkg/app lists
- Different concerns, different iteration patterns, different output types

### Why Defense-in-Depth for Protected Paths?

Protected paths are checked at two levels:
1. **CLI** (`_domain_clean()`): User-visible "Skipping protected" warnings
2. **Operator** (`FilesystemOperator.delete()`, `ConfigOperator._delete_single()`): Hard rejection

This prevents accidental deletion even if CLI-level filtering is bypassed or refactored.

---

## Testing Strategy

| Category | Location | Target |
|----------|----------|--------|
| Unit tests | `tests/unit/` | 85%+ per module |
| Shared fixtures | `tests/unit/conftest.py` | `sample_manifest` with APT + Flatpak packages |

**Key patterns:**
- `typer.testing.CliRunner` for CLI tests
- `tmp_path` fixture for file I/O tests
- `mocker.patch("subprocess.run")` for shell command tests
- Never call real package managers in tests
- Parametrized domain tests: `@pytest.mark.parametrize("domain", ["filesystem", "configs"])`

**Current metrics:**
- Coverage: 92%
- Pyright: 0 errors in `app/`
- Ruff: Clean

---

## File Locations Summary

| Purpose | Path |
|---------|------|
| Main manifest | `~/.config/popctl/manifest.toml` |
| Advisor config | `~/.config/popctl/advisor.toml` |
| Action history | `~/.local/state/popctl/history.jsonl` |
| Config backups | `~/.local/state/popctl/config-backups/<timestamp>/` |
| System backups | `~/.local/state/popctl/backups/` (default local target) |
| Backup config | `~/.config/popctl/backup.toml` |
| Backup recipients | `~/.config/popctl/backup.age-recipients` (fallback) |
| Advisor sessions | `~/.djinn/sessions/popctl/<timestamp>/` |
| Advisor memory | `~/.local/state/popctl/advisor/memory.md` |
| Default theme | `app/popctl/data/theme.toml` |
