# IMPLEMENTATION.md — popctl Deep Dive

This document provides a comprehensive technical reference for the popctl implementation, covering architecture, data flow, module responsibilities, and design decisions.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Layer Breakdown](#layer-breakdown)
3. [Data Models](#data-models)
4. [Core Modules](#core-modules)
5. [Package Scanners & Operators](#package-scanners--operators)
6. [Sources Subsystem](#sources-subsystem)
7. [Domain Layer (Filesystem & Configs)](#domain-layer-filesystem--configs)
8. [AI Advisor System](#ai-advisor-system)
9. [The Sync Pipeline](#the-sync-pipeline)
10. [CLI Commands](#cli-commands)
11. [Dotfiles](#dotfiles)
12. [Desktop Alerts](#desktop-alerts)
13. [Data Flow Examples](#data-flow-examples)
14. [Design Decisions](#design-decisions)

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
│     Scanners/Operators/Sources/Advisor/Filesystem/Configs/Backup     │
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
| `setup.py` | `popctl setup` |
| `advisor.py` | `popctl advisor {classify,session,apply}` |
| `alerts.py` | `popctl alerts {watch,init-config,install-service,test}` |
| `dotfiles.py` | `popctl dotfiles {init,status,sync,apply}` |
| `fs.py` | `popctl fs {scan,clean}` |
| `config.py` | `popctl config {path,show,edit,scan,clean}` |
| `backup.py` | `popctl backup {init,create,restore,list,info}` |
| `manifest.py` | `popctl manifest {keep,remove}` |
| `history.py` | `popctl history` |
| `undo.py` | `popctl undo` |
| `doctor.py` | `popctl doctor` |

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
| `paths.py` | XDG path resolution (`get_config_dir()`, `get_data_dir()`, `get_state_dir()`, `get_manifest_path()`, `ensure_dir()`) |
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
| `restore.py` | `restore_backup()` — decrypt, decompress, then restore state → sources → packages → home files; `read_backup_metadata()`, `list_backups()` |

### Sources (`sources/`)

| File | Responsibility |
|------|----------------|
| `models.py` | Strict frozen Pydantic records for captured source state, locators, bindings, and replay modes |
| `capture.py` | Fail-closed capture of public APT, Flatpak, and Snap source state plus the narrow APT parser |
| `keytrust.py` | Isolated OpenPGP inspection, minimal public-key export, fingerprint verification, and safe key-path resolution |
| `diff.py` | Source `missing` / `extra` / `changed` diff by stable locator, including APT provenance diagnostics |
| `preflight.py` | Selected-manager, platform, suite-compatibility, trust, and Flatpak relationship barrier |
| `provision.py` | Managed APT key/stanza and Flatpak remote reconciliation, strict APT index refresh, retained-artifact results |
| `phase.py` | Shared capture/trust, refresh, preview, confirmation, preflight, and provision orchestration |

### Dotfiles (`dotfiles/`)

| File | Responsibility |
|------|----------------|
| `config.py` | Strict `DotfilesConfig` TOML loading and atomic saving, including the desktop-settings allowlist and toggle; defaults the bare store from `get_data_dir()` |
| `desktop.py` | Desktop-settings artifact v1, canonical dconf-root policy, capture/load result models, and all dconf command boundaries |
| `state.py` | Process lock, immutable apply/inbound-sync plans, completed-paths journals, and init finalization recovery |
| `secret_filter.py` | Fail-closed path and content admission filter for every candidate, commit, remote tree, and apply source |
| `discovery.py` | Bounded, deterministic exact-file candidate discovery under the supported `$HOME` roots |
| `repo.py` | The only controlled Git interface: validation, reserved-entry partition/admission, private-index commits, refs, and isolated transport |
| `materialize.py` | Ref-tree preflight and fd-anchored, atomic per-file writes into `$HOME` |

### Infrastructure (`utils/`)

| File | Responsibility |
|------|----------------|
| `shell.py` | Text `run_command()` (including text stdin) and byte-preserving `run_command_bytes()` subprocess wrappers with typed results and timeouts |
| `desktop.py` | Pure GNOME/COSMIC/unknown family normalization from XDG desktop signals |
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
class SourceInstallContext:
    flatpak_remote: str | None = None
    flatpak_scope: FlatpakScope | None = None
    flatpak_arch: str | None = None
    flatpak_branch: str | None = None
    snap_channel: str | None = None

@dataclass(frozen=True, slots=True)
class Action:
    action_type: ActionType
    package: str
    source: PackageSource
    source_install_context: SourceInstallContext | None = None
    # Validates: PURGE only for APT and SNAP

@dataclass(frozen=True, slots=True)
class ActionResult:
    action: Action
    success: bool
    detail: str | None = None

    @property
    def failed(self) -> bool: ...
```

`SourceInstallContext` is populated only for installs that have a recorded source context. A
Flatpak context is all-or-nothing (`remote`, `scope`, `arch`, and `branch`); a Snap context is its
channel. It cannot mix the two. `diff_to_actions()` keeps duplicate Flatpak application IDs distinct
when their `(scope, id, arch, branch)` records differ, and the executor passes those `Action`
objects through to the single-action Flatpak and Snap operators. A missing context deliberately
keeps the legacy bare install behavior.

### History Models (`models/history.py`)

```python
class HistoryActionType(Enum):
    INSTALL = "install"
    REMOVE = "remove"
    PURGE = "purge"
    ADVISOR_APPLY = "advisor_apply"
    FS_DELETE = "fs_delete"
    CONFIG_DELETE = "config_delete"
    DOTFILES_INIT = "dotfiles_init"
    DOTFILES_SYNC = "dotfiles_sync"
    DOTFILES_APPLY = "dotfiles_apply"

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
Dotfiles entries hold home-relative paths and ref metadata and are always non-reversible; the CLI
therefore labels the shared table as Action History / Items rather than package-specific terms.

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
    sources: SourcesConfig | None = None
```

`sources` is a root-level optional section, not an extension of `PackageEntry`. It is strict at
every structural level and therefore makes an older binary fail loudly rather than silently dropping
source state on a later save. `None` is the backward-compatible empty source context: it triggers no
source diff, preflight, prompt, manager requirement, or provisioning.

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
get_data_dir()      # ~/.local/share/popctl/
get_state_dir()     # ~/.local/state/popctl/
get_manifest_path() # ~/.config/popctl/manifest.toml
ensure_dir(path, name) -> Path  # mkdir -p with error handling
```

### baseline.py — Protected Package Management

Defines system-critical packages that must never be removed:

- **Pattern matching:** `linux-*`, `systemd*`, Pop!_OS/COSMIC (`pop-*`, `cosmic-*`), GNOME, KDE Plasma, `apt*`, `dpkg*`, etc.
- **Exact matches:** `bash`, `coreutils`, `sudo`, `snapd`, etc.
- **Used by:** `compute_diff()` (filters from diff), `apply` and `undo` (blocks removal)
- **Transaction guard:** the APT operator additionally simulates every remove/purge
  (`apt-get -s`) and refuses the whole transaction if the resolver would remove any
  protected package as a dependent — so removing an unprotected package can never drag
  a protected one out. A failed or unparseable simulation refuses fail-safe.

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
def diff_to_actions(diff_result, purge=False, sources=None) -> list[Action]
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
- A source-aware Flatpak install gets the recorded remote, scope, architecture, and branch; a
  source-aware Snap install gets its recorded channel. Ambiguous or non-replayable recorded context
  is an error instead of a silently different install.

### executor.py — Action Execution

```python
def execute_actions(actions: list[Action], operators: list[Operator]) -> list[ActionResult]
def record_actions_to_history(results: list[ActionResult], command: str) -> None
```

**Execution flow:**
1. Group actions by `source` (`defaultdict(list)`)
2. For each operator, split into `install_pkgs`, `remove_pkgs`, `purge_pkgs`
3. Call `operator.install()`, `operator.remove(purge=False)`, `operator.remove(purge=True)`
4. Synthesize a failed result for every planned action that no operator returned, so a missing
   selected manager cannot become a silent omission.

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
| `FlatpakOperator` | recorded `remote`, `--user`/`--system`, `--arch`, and `--branch`, or bare `--user` | `flatpak uninstall -y -- <pkg>` | N/A |
| `SnapOperator` | `sudo snap install --channel=<channel> -- <pkg>`, or bare install | `sudo snap remove -- <pkg>` | `sudo snap remove --purge -- <pkg>` |

---

## Sources Subsystem

`sources/` is a separate concern from installed-package scanners: it records where packages come
from and reconstructs only eligible third-party sources before package execution. The records are
frozen Pydantic models because they are manifest-boundary data.

### Data Model and Identity

`SourcesConfig` carries the captured `SourcePlatform(distro_id, codename)` and manager subsections:

- `AptSources.entries` keeps the capture path, format (`legacy` or `deb822`), ordinal, verbatim
  stanza, `SignedByBinding`, referenced `AptKey` IDs, replay mode, and optional PPA display value.
  Each entry has two identities: its capture `(path, ordinal)` locator and its durable
  `managed_target` locator used after popctl writes a generated `sources.list.d` file.
- `AptSources.keys` stores only public armored material, full uppercase fingerprints, and a managed
  `/etc/apt/keyrings/<id>.asc` target. `SignedByBinding` preserves resolved source paths,
  fingerprint selectors (including `!`), or embedded armor.
- `FlatpakSources.remotes` are located by `(scope, name)` and retain URL, GPG verification state,
  verified public armor/fingerprints, and replay mode. `FlatpakSources.apps` are located by
  `(scope, id, arch, branch)`, so identical app IDs and branches never collapse.
- `SnapSources.packages` are located by name and retain the full tracking channel and replay mode.

Every APT source, Flatpak remote, and Snap channel is `report-only`, `replay`, or `blocked`; Flatpak
apps supply installation context and inherit their remote's eligibility. The APT classifier is
deliberately bounded: it normalizes URI hosts and paths, requires current-codename suites, and
accepts only the explicit canonical Debian, Ubuntu, or Pop!_OS archive URI and available Origin sets.
A match is `report-only` regardless of its captured filename; every nonmatch or unrecognized platform
is `replay`. Insecure APT options or `no-gpg-verify` Flatpak remotes are `blocked`. Only `replay`
records are provisioned.

### Capture and Key Trust

`capture_sources()` selects APT, Flatpak, and/or Snap managers, captures the platform identity, and
returns one `SourcesConfig`. Its gates run before serializing a candidate:

1. The dependency-free APT parser reads `/etc/apt/sources.list` and `.list` / paragraph-aware
   `.sources` files under `sources.list.d`, retaining enabled stanzas, comments/options, and their
   ordinal. Malformed source syntax fails closed.
2. It rejects URI userinfo or queries, known APT authentication options, and URI host/path matches
   from selector-only `/etc/apt/auth.conf{,.d}` parsing. An unreadable auth store is a capture error;
   auth values are never retained. APT entries without `Signed-By` are refused.
3. `keytrust.py` uses `lstat` plus realpath and accepts only regular key files beneath supported
   keyring roots. It rejects secret OpenPGP packets before import, then uses an isolated temporary
   `GNUPGHOME` to inspect, minimally export, and fingerprint public material. A selector exports
   exactly its selected fingerprints; no selector exports the complete public set.
4. Flatpak capture reads only remote `name`, `url`, and `options` in each user and system scope, then
   records apps with origin/scope/architecture/branch. It rejects authenticated options and exports
   the scope-local OSTree `<remote>.trustedkeys.gpg` keyring as the primary anchor. A verified
   `.flatpakrepo` `GPGKey` is a fallback only when that keyring cannot provide material.
5. Snap capture reads the `Tracking` column from `snap list`, omitting runtime snaps. A missing
   tracking channel is an error.

`capture_and_trust_sources()` is used by `init` and sync bootstrap. It shows each replayable
third-party identity and fingerprint before an interactive trust confirmation. It rejects blocked
records, and a non-interactive or `--yes` bootstrap cannot create a new trust record. Dry-run returns
the ephemeral captured section without a prompt or manifest save.

### Diff, Preflight, and Provisioning

`compute_source_diff()` compares stable locators separately from their attributes and reports
`missing`, `extra`, and `changed`. A changed URI, suite, key fingerprint, remote URL, or channel
stays `changed` rather than becoming a removal/addition pair; a locator-changing scope is naturally
an addition/removal pair. Extra sources are report-only:
the provisioner has no removal action. For unrecorded live APT sources, the bounded read-only
`apt-cache policy <package>` resolver maps a candidate origin to an APT capture locator when the
mapping is unique; ambiguous or unavailable provenance is `unknown`.

`run_source_phase()` is the shared entry point for `sync`, `apply`, and package-bearing restore:

1. It treats `Manifest.sources is None` or an empty selected manager set as a successful no-op.
2. It determines selected managers from `SourceChoice`, requires each manager, captures live source
   state, and runs platform, suite, key, remote, and channel checks as one all-source barrier before
   any write. APT requires matching distro IDs plus either matching distro-tied suites/codename or a
   `stable` suite; when the target already has the same managed `stable` record, its URI must match.
3. It computes source drift and renders one preview containing source states, fingerprints, and
   planned source commands. Dry-run stops after this read-only stage.
4. It asks per changed source before trust-relevant replacement; `--yes` and non-interactive mode
   fail closed for a new or changed relationship. `sync` additionally runs its selected-manager live
   refresh before this phase in normal existing-manifest mode, asks per live addition/change, and
   atomically persists only confirmed replacements while leaving extras untouched.
5. Provisioning writes only missing records, skips exact matches, and replaces a changed APT target
   only when popctl owns its generated managed target. An unmanaged conflict fails without a duplicate
   stanza. It writes public APT keys as root-owned mode `0644`, re-reads and fingerprint-verifies them,
   rewrites exactly one `signed-by=` managed stanza, and imports the exact Flatpak key before adding
   the scoped remote. `apt-key` and the primary distribution source file are never used.
6. When APT is selected, `sudo apt-get update --error-on=any` is mandatory after reconciliation. A
   source command or strict-index failure returns retained operation-owned artifacts and stops all
   later package and home work; there is intentionally no rollback.

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
- **Common:** `~/.ssh/*`, `~/.gnupg/*`, COSMIC (`~/.config/cosmic*`), GNOME (`~/.config/dconf` and its contents, `~/.config/gnome-session`), KDE Plasma session files (`kdeglobals`, `kglobalshortcutsrc`, `kwinrc`, `kwinrulesrc`, `plasmashellrc`, the Plasma applets rc), `~/.config/popctl` and `~/.local/state/popctl` (incl. session workspaces) — deliberately narrow session-critical entries, so app configs such as `kdeconnect`, `kdenlive`, `gnome-builder` and per-user GNOME Shell extensions remain cleanable
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
│              │     │  ~/.local/state/popctl/sessions/│     │ (Claude/     │
│  (sync or   │     │  <timestamp>/                 │     │  Gemini/     │
│   advisor)  │◀────│    CLAUDE.md                  │◀────│  Codex)      │
│              │     │    scan.json                  │     │              │
│              │     │    manifest.toml              │     │              │
│              │     │    memory.md                  │     │              │
│              │     │    output/decisions.toml      │     │              │
└──────────────┘     └─────────────────────────────┘     └──────────────┘
```

Advisor workspaces default to `~/.local/state/popctl/sessions/`. When the optional
`djinn-in-a-box` session backend is active, they use `~/.djinn/sessions/popctl/` because
that host directory is bind-mounted into the Djinn container, making workspaces accessible
from both host and container; without djinn, the agent runs directly on the host.

`advisor/session_protocol.py` defines local Protocols for the optional session manager and
its result. `advisor/_djinn_backend.py` lazily imports the optional `djinn-in-a-box` backend
and returns a typed session manager when it is available; otherwise, `AgentRunner` uses host
execution. The prompt templates are generic and English-only; interactive templates direct the
advisor to use ASK rather than REMOVE when the available evidence is uncertain.

### Workspace Structure

Each advisor invocation creates an ephemeral session directory:

```
~/.local/state/popctl/sessions/20260223T143000/
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

### Provider Commands and Authentication

`AdvisorConfig` supports `claude`, `gemini`, and `codex`. On direct host execution, the command
shapes and optional API-key environment variables are:

| Provider CLI | Headless command shape | Interactive command shape | `api_key` environment variable |
|--------------|------------------------|---------------------------|--------------------------------|
| `claude` | `claude -p <prompt> --output-format json [--model <model>]` | `claude <prompt> [--model <model>]` | `ANTHROPIC_API_KEY` |
| `gemini` | `gemini --prompt <prompt> [--model <model>]` | `gemini --prompt <prompt> [--model <model>]` | `GEMINI_API_KEY` |
| `codex` | `codex exec --skip-git-repo-check --model <model> <prompt>` | `codex --model <model> <prompt>` | `OPENAI_API_KEY` |

The model flag is optional for Claude and Gemini unless a model is configured. Codex always passes
one, defaulting to `gpt-5.6-terra`. A configured `api_key` is passed only to the selected child
CLI; it is unnecessary when that CLI is already logged in. When an interactive host session cannot
find the selected CLI, it provides a `popctl doctor` / `--no-advisor` hint instead of a raw command
failure.

### Cross-Session Memory

The advisor maintains persistent memory at `~/.local/state/popctl/advisor/memory.md`. When creating a workspace:
1. Try persistent `memory.md` first
2. Fallback: copy from most recent previous session (chaining for host-mode)
3. Warn if memory exceeds 50 KB

---

## The Sync Pipeline

The `sync` command (`cli/commands/sync.py`) is the main orchestrator. Its source work always
precedes package and home work:

```
sync()
├── _ensure_manifest()                    — auto-init if missing
│   └── capture_manifest()                 — package scan + capture_and_trust_sources()
├── refresh_manifest_sources()             — existing manifest, normal mode only
│   └── capture selected live sources → confirm additions/changes → atomic merge
├── run_source_phase()
│   └── manager/platform/trust preflight → source diff/preview → reconcile → strict APT update
├── _sync_packages()
│   ├── compute_system_diff()              — NEW/MISSING/EXTRA
│   ├── _run_advisor()                     — AI classifies NEW packages
│   ├── _apply_advisor_decisions()         — write to manifest
│   ├── compute_system_diff()              — re-diff after changes
│   ├── diff_to_actions()                  — source-aware actions + user confirmation
│   ├── execute_actions()                  — install/remove/purge
│   └── record_actions_to_history()
├── _run_orphan_phases("filesystem")
│   ├── _domain_scan()                     — FilesystemScanner
│   ├── _domain_run_advisor()              — AI classifies orphans
│   ├── _domain_apply_decisions()          — write to manifest
│   ├── _domain_clean()                    — delete paths
│   └── _record_orphan_history()
└── _run_orphan_phases("configs")
    ├── _domain_scan()                     — ConfigScanner
    ├── _domain_run_advisor()              — AI classifies orphans
    ├── _domain_apply_decisions()          — write to manifest
    ├── _domain_clean()                    — backup + delete
    └── _record_orphan_history()
```

### Error Handling Philosophy

| Phase | Severity | Behavior |
|-------|----------|----------|
| Init / source capture | **Fatal** | Capture or trust failure → `Exit(1)`; dry-run capture is ephemeral |
| Source refresh / preflight / provision | **Fatal** | A manager, compatibility, trust, source command, or strict APT index failure → `Exit(1)` before package or orphan work |
| Package diff | **Fatal** | Scanner/manifest error → `Exit(1)` |
| Advisor | **Non-fatal** | Warning printed, sync continues with existing manifest |
| Package confirmation | **User abort** | `Exit(0)`, propagates past orphan phases |
| Package execution | **Per-action** | Failed or unhandled actions are collected; unhandled actions make sync exit 1 |
| History | **Non-fatal** | Warning on write failure |
| Orphan scan | **Non-fatal** | Scan failure returns `None` (skip remaining phase with warning); a clean system returns `[]` |
| Orphan phases | **Non-fatal** | Each sub-phase catches its own errors |

### CLI Flags

| Flag | Effect |
|------|--------|
| `--yes` / `-y` | Skip ordinary confirmations; it cannot approve a new or changed source trust relationship |
| `--dry-run` / `-n` | Run read-only source preflight/preview and show package/diff results without mutation |
| `--source` / `-s` | Filter both source records and package work to APT/Flatpak/Snap/All |
| `--purge` / `-p` | Use purge instead of remove (APT/Snap) |
| `--no-advisor` | Skip all AI advisor phases |
| `--auto` / `-a` | Use headless advisor (no interaction) |
| `--no-filesystem` | Skip filesystem orphan phases |
| `--no-configs` | Skip config orphan phases |

---

## CLI Commands

### sync (`cli/commands/sync.py`)

The main orchestrator. See [The Sync Pipeline](#the-sync-pipeline) for details.

### setup (`cli/commands/setup.py`)

Guides first-time users through core-tool checks, advisor provider and authentication selection,
optional manifest creation, desktop-alert and encrypted-backup delegation, and optional interactive
dotfiles initialization. When stdin is not a TTY, it prints a static numbered guide instead of
prompting.

### init (`cli/commands/init.py`)

| Option | Description |
|--------|-------------|
| `--output PATH` | Custom output path |
| `--dry-run` | Preview without creating |
| `--force` | Overwrite existing manifest |

Scans available package managers, filters to manual packages, excludes protected packages, then uses
the shared capture-and-trust workflow to attach selected source records before atomically saving the
manifest. `--dry-run` returns the captured manifest only in memory and never confirms or saves a
source trust relationship.

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

The command combines the package diff with `compute_source_system_diff()`. Source rows preserve
their stable locators and add `missing`, `extra`, and `changed` states; JSON places the source result
under `sources`.

### apply (`cli/commands/apply.py`)

| Option | Description |
|--------|-------------|
| `--yes` | Skip confirmation |
| `--source` | Apply only specific source |
| `--purge` | Use purge instead of remove |
| `--dry-run` | Preview only |

`apply` invokes `run_source_phase()` before calculating package actions, passing the selected
`SourceChoice`, dry-run state, and interactive policy. A failed source phase exits before package
operators are selected.

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

**`popctl config path`** — List the manifest, advisor, alerts, backup, dotfiles, and theme
configuration locations with their current existence status.

**`popctl config show [name]`** — Print one of those files; the advisor `api_key` value is
structurally redacted, and an unparseable file is reported instead of dumped raw.

**`popctl config edit [name]`** — Open the named file in `$EDITOR` (falls back to `$VISUAL`,
then `nano`); refuses without an interactive terminal.

The absent `dotfiles.toml` case is deliberately read-only: `popctl config edit dotfiles` prints
the `popctl dotfiles init` hint and does not create a partial configuration. Once initialization has
created the file, it follows the ordinary editor path.

Note: when the optional djinn session backend is active AND an `api_key` is configured, the
advisor warns and runs on the host instead — the session interface cannot yet receive a
provider-scoped environment (tracked upstream: djinn_in_a_box issue #7).

### alerts (`cli/commands/alerts.py`)

**`popctl alerts init-config`** — Materialize the packaged `alerts.toml` template at
`~/.config/popctl/alerts.toml`; `--force` permits replacement of an existing file.

**`popctl alerts install-service`** — Materialize the packaged systemd user-service template at
`~/.config/systemd/user/popctl-alerts.service`, substituting the resolved `popctl` executable path.
It attempts a user-unit reload and otherwise prints manual systemd instructions.

**`popctl alerts test`** — Fire a sample notification and sound; `--kind` accepts `preeve`, `pre`,
or `warning`.

### backup (`cli/commands/backup.py`)

**`popctl backup init`** — Generate `~/.config/age/key.txt` (mode `0600`) and
`~/.config/popctl/backup.toml`; it refuses to overwrite either file and accepts `--yes` / `-y` to
skip confirmation.

**`popctl backup create`** — Create an encrypted backup (tar|zstd|age pipeline). Supports local paths and rclone remotes. Auto-prunes old backups based on `max_backups` config.

**`popctl backup restore <source>`** — Restore from an encrypted backup. It exposes `--source` and
`--dry-run` in addition to `--files-only` and `--packages-only`. After extraction, it loads
`staging/files/popctl/manifest.toml` once and passes that in-memory manifest to both the source and
package phases; it never switches to the target machine's manifest after state restore. Package
bearing modes run state → sources → packages → home, while files-only runs state → home → permissions
with no source work. Dry-run carries through every state, source, package, home, permission, and
history mutation.

**`popctl backup list`** — List available backups at a target location.

**`popctl backup info <source>`** — Show backup metadata without restoring.

### dotfiles (`cli/commands/dotfiles.py`)

**`popctl dotfiles init [--remote URL]`** — Discover safe exact files, obtain reviewed
track/ignore/ask decisions, create a checked initial commit in a new bare store, and connect it to
a private GitHub repository. `--remote` and `--from` are mutually exclusive.

**`popctl dotfiles init --from URL`** — Bootstrap a fresh machine from an existing popctl-format
repository after the same privacy gate, marker validation, and full-tree validation. It does not
run a curation pass; `apply` materializes the validated home-file source and establishes local
`main`. A valid desktop-settings artifact remains in the repository and is not a `$HOME` file.

**`popctl dotfiles status`** — Fetch only remote refs with `--no-write-fetch-head`, then report
branch relation, tracked-path state, new candidates, and blocked candidates without modifying
`$HOME`, the local branch, `HEAD`, configuration, or history. It separately reports the
desktop-settings artifact as absent, invalid, or present with family, root count, and the artifact
path's last-changing revision age.

**`popctl dotfiles sync`** — Fetch and classify before any local mutation. It refuses diverged
histories, both-changed paths, and remote deletion of a tracked path; compatible inbound changes are
materialized without a checkout, eligible local changes are checked into one commit, then an
automatic push is attempted. After validation and reconciliation it independently captures enabled
desktop settings, so an artifact-only change can commit and push even if candidate review is
cancelled. Offline sync can use cached refs and preserves pending work.

**`popctl dotfiles apply [--dry-run]`** — Enforces package-first ordering, fetches or uses a
cached validated source, emits the deterministic no-clobber plan for `--dry-run`, and otherwise
materializes directly from that ref. After a successful real materialization, including a no-op, it
independently attempts the enabled desktop load. Dry-run previews its desktop verdict without a dconf
call. It never executes repository content or deletes a target.

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

### doctor (`cli/commands/doctor.py`)

Reports readiness of core package management and optional features; only missing core
package-management tools cause exit status 1. Its dotfiles section checks Git, configuration, bare
repository, remote validity, and a five-second controlled `ls-remote` probe. Offline,
authentication, timeout, and other reachability results remain distinct warnings. It also explains
that absent `gh` means automatic pushes cannot recheck GitHub visibility and recommends installing
it for per-push privacy verification. Its separate desktop-settings section reports the config
toggle, `dconf` availability (with the `dconf-cli` install hint), a user-session hint, and the
normalized desktop family; all are nonfatal optional-feature findings.

---

## Dotfiles

`popctl dotfiles` is a separate, private user-configuration subsystem. Its Git work tree is always
the user's `$HOME`, but it has no checkout workflow: the only persistent Git store is the bare
repository at `get_data_dir() / "dotfiles.git"`. Tracked names are canonical, home-relative,
regular text files. The subsystem does not manage `/etc`, symlinks, binaries, templating, or merge
resolution. The sole exception is the generated desktop-settings artifact: it is a repository-only
entry, not a tracked or materialized home file.

### Configuration and repository contract

`dotfiles/config.py` owns the strict, atomic `dotfiles.toml` format. Its `DotfilesConfig` records
the bare repository path, canonical GitHub remote URL, exact-path ambiguous-content allowlist,
ignored paths, a remote-privacy record, and a strict `desktop_settings` sub-model. The latter has
`enabled = true`, `extra_roots = ()`, and `disabled_roots = ()` by default. Configured roots
(`DEFAULT_ROOTS + extra_roots`) and reductions (`disabled_roots`) are validated independently for
the shared canonical dconf-directory grammar, duplicates, and ancestor/descendant overlap; a root
may validly appear in both collections. The effective roots are the deterministic sorted result of
defaults plus extras minus disabled roots. Bare `/`, bare `/org/gnome/shell/`, relative roots,
non-slash-terminated roots, repeated separators, dot segments, backslashes, whitespace, and control
characters are rejected.

There are no credential fields. The privacy record is either `verified` by
`gh repo view --json isPrivate` or `acknowledged` after the user explicitly accepts an
unverified-private exact URL. The latter is standing consent only for that canonical URL; a remote
change invalidates it. With `gh`, initialization and every automatic push fail closed unless the
repository is currently private.

`dotfiles/repo.py` owns every Git operation. It permits canonical GitHub HTTPS and GitHub SSH URLs
only, uses explicit `main` and format-marker refspecs, and verifies the
`popctl-dotfiles-format-v1` marker before treating a remote as a compatible popctl repository. The
marker is format compatibility, not cryptographic provenance.

### Desktop-settings artifact, namespace, and admission

`dotfiles/desktop.py` defines the v1 artifact at the exact repository path
`.config/popctl/desktop-settings.dconf`. It renders UTF-8, LF-only keyfile text with no NUL bytes,
bounded to 1 MiB:

```text
# popctl-desktop-settings v1
# family: GNOME
# root: /org/gnome/desktop/interface/
# end-header
# root: /org/gnome/desktop/interface/
[interface]
gtk-theme='Example'
```

The header records `GNOME`, `COSMIC`, or `UNKNOWN` and the complete ordered root list. Each matching
`# root:` section contains verbatim `dconf dump <root>` output; an empty body still has its root
marker. The bounded parser requires the version and complete header, canonical unique roots, matching
header and section order, and valid UTF-8/LF bodies before any dconf operation. Artifact rendering
sorts sections by root, so an upstream dconf ordering change can cause a one-time artifact-only
commit even when the effective settings did not change.

The path is a narrow reserved namespace, not a new home-file policy. `partition_tree_entries()`
keeps it in the full tree for validation and artifact lookup while passing only home entries to
tracking, source/base snapshots, classification, conflict/deletion handling, status file accounting,
inbound sync, materialization, and ordinary history path lists. The artifact is never written to
`$HOME/.config/popctl/desktop-settings.dconf`. A pre-feature client retains its normal
`.config/popctl/**` deny rule and therefore refuses a tree containing this artifact; once an
artifact has been pushed, clients must be upgraded before they can sync that repository.

`DotfilesRepo.validate_tree()` and `checked_commit()` share exact-path admission. The exception
accepts only this regular mode-`100644` file, asks Git for its blob size before reading it, then
strictly parses it and invokes the secret filter's private content-only seam. The public scanner
continues to deny `.config/popctl/**`, so this does not create a general path bypass. Generic hard
secret findings remain terminal. Parsed bodies retain root provenance: ambiguous content at a
built-in root is admitted only in memory, while an `extra_roots` section needs an explicit,
section-scoped interactive acknowledgement and is rejected non-interactively. This validation and
secret admission remains active when desktop operations are disabled.

Bootstrap keeps three concepts separate. A **remote-declared candidate root** is a non-default root
parsed from the remote artifact and is transient discovery data, not consent. An **adopted root** is
one of the complete displayed candidate set that an interactive `dotfiles init --from` user confirms
in its single batch decision; only adopted roots are persisted in
`[desktop_settings].extra_roots`. A **transient ambiguous-content acknowledgement** is the separate,
section-scoped admission decision for ambiguous artifact content. It admits that content for the
operation but neither adopts nor persists a root. An ambiguous candidate therefore needs both the
content acknowledgement and the independent adoption decision. This preserves slice #4's
non-persisting acknowledgement contract: it applies to content admission, not to a genuinely adopted
root.

The strict local `desktop_settings` allowlist is the trust boundary between a remote artifact and
dconf. A remote declaration cannot cross it merely by being parsed or admitted. Non-interactive
bootstrap and a declined batch choice persist no candidates; policy-compatible roots can later be
added deliberately to `extra_roots`, while policy-incompatible declarations must first be corrected
to a compatible, non-overlapping subset. Before promotion, the confirmed set is reconstructed as a
`DesktopSettingsConfig`; rejection aborts bootstrap without promoting the store or configuration.

All root-bearing CLI values pass through one literal root display projection. The projection is
scoped by value type rather than provenance, so it covers remote candidates, adopted local roots,
capture `root` and `detail` fields, and root-containing validator or admission diagnostics. It
produces literal markup-disabled text before prompts, reports, and errors render it, while fixed
copy keeps its own styling. This prevents Rich markup in a root from changing the consent display or
raising a rendering error.

The built-in `DEFAULT_ROOTS` deliberately contains only credential-free GNOME desktop state:

- `/org/gnome/desktop/wm/keybindings/`
- `/org/gnome/settings-daemon/plugins/media-keys/`
- `/org/gnome/desktop/interface/`
- `/org/gnome/desktop/wm/preferences/`
- `/org/gnome/desktop/input-sources/`
- `/org/gnome/desktop/background/`
- `/org/gnome/desktop/screensaver/`

The broad `/org/gnome/shell/` root is excluded because it includes extension state.

`utils/desktop.py` has no desktop side effects. It normalizes `XDG_CURRENT_DESKTOP` and
`XDG_SESSION_DESKTOP` token-by-token: `ubuntu`, `pop`, and `gnome` resolve to GNOME, `cosmic` to
COSMIC. Missing signals, unknown tokens, conflicts within one signal, or disagreement between the
two signals resolve to `UNKNOWN`.

### Checked-commit gateway and secret filtering

The checked-commit gateway is the only path that creates local dotfiles commits. It reads each
approved home file through fd-anchored regular-file checks, snapshots immutable bytes, hashes those
bytes into a private temporary index, validates the resulting tree, creates a commit, and
conditionally advances `refs/heads/main`. It does not stage a live `$HOME` work tree, so a change
after the snapshot cannot silently become part of the commit.

`dotfiles/secret_filter.py` is defense in depth, not advisor policy. It runs before candidate
exposure, before staging, over complete committed and fetched trees, and before apply. The pipeline:

1. Rejects canonical paths matching hard deny globs (keys, credential stores, browser login data,
   popctl state, and similar secret-bearing locations), non-regular files, oversized files, binary
   data, and unreadable paths. These key-material path classes are a hard guarantee.
2. Normalizes line endings, examines raw assignment pairs, and applies hard recognizers for private
   key blocks, AGE keys, known tokens, authorization and Git extra headers, proxy credentials,
   curl credentials, and URL userinfo. Authorization and proxy-auth values remove enclosing quotes
   and leading/trailing HTTP optional whitespace before Bearer/Basic recognition, so those findings
   remain terminal and non-allowlistable.
3. Parses JSON, YAML, TOML, dotenv, and selected INI files fail closed. Parsed scalars and key/value
   pairs re-enter the hard recognizers, including YAML's semantic scalars. Duplicate
   credential-shaped fields are a hard rejection rather than a last-value-wins ambiguity.
4. Finds maximal standard or URL-safe base64-alphabet runs and bounded valid subspans at whitespace
   or `=` boundaries, collapses every interior ASCII whitespace form, canonically decodes sufficiently
   long runs with recovered padding, and recursively scans to depth two; a further decodable layer is
   rejected. This closes whitespace-grouped and common prose/interior-padding base64 without
   per-group-size grammar variants.
5. Returns ambiguous findings only for an explicit canonical-path allowlist acknowledgement. An
   allowlist can never override a hard finding, malformed structured content, or an unsafe path.

Curl command credentials are tokenized with POSIX shlex after backslash-newline continuation
normalization, with the existing non-POSIX fallback. The parser covers user and proxy-user short,
attached, clustered, long, and unambiguous abbreviated option forms, including empty-user passwords,
command-substituted values, bounded nested `sh -c` and substitution commands, operator-separated
commands, and JSON/YAML/TOML argv arrays. The `.curlrc` matcher recognizes the corresponding user and
proxy-user abbreviations.

The embedded-credential content scanner is a conservative, fail-closed, best-effort
defense-in-depth control, not a completeness guarantee. It covers all known raw credential/token
formats, generic whitespace-obfuscated base64, and the common realistic shell/curl forms above.
Novel encoding or prose-embedding obfuscations of a self-authored credential remain an accepted
residual, mitigated by the private-repository model, advisor curation, and mandatory explicit
per-file review during `init`. ANSI-C `$'...'` quoting and arbitrarily nested prose or encoding
embeddings are the accepted residual class. This documented best-effort scanner boundary is frozen.

### Transport isolation

Network Git calls are deliberately narrower than ordinary user Git. The repository builds a positive
environment allowlist, supplies owned global Git configuration with hooks disabled, disables terminal
prompts, and invokes SSH with an owned `ssh -F` configuration plus batch mode. Before each connection
it validates the bare repository's local configuration against the small owned-key set, rejecting
unexpected hooks, URL rewrites, proxy/SSH overrides, or local configuration injection. Existing user
credential helpers are carried into the owned network configuration; popctl neither stores nor prints
tokens. `ls_remote`, fetch, and push return typed success, offline, authentication, timeout, or other
transport outcomes.

### Ref materialization and recovery

Remote content is never merged or checked out into `$HOME`. The inbound path is:

```
fetch ref → validate marker/tree/secrets → classify refs and paths → preflight targets
          → materialize exact blobs → conditionally advance local ref
```

`materialize.py` first creates an immutable per-path plan from the validated ref. A target may be
created, left as a matching no-op, or replaced only when it still equals the tracked base; an
untracked or locally changed differing target is refused. Writes traverse `$HOME` through directory
file descriptors with `O_NOFOLLOW`, recheck the target fingerprint immediately before `os.replace`,
write an atomic per-file temporary replacement, and preserve the tracked executable mode. A source
tree that drops a tracked path is refused rather than deleting a home file.

`state.py` serializes mutations with an OS-released `flock`. Apply and inbound-sync plans include
the source ref/tree and exact path/OID/mode/action/fingerprint entries, with completed-paths journals
written beside them. Recovery resumes a matching immutable plan or refuses changed targets; it never
rolls back already-completed safe files. Initialization has a separate finalization journal that
records only popctl-owned temporary/final stores and configuration transition, so interrupted setup
cleans unfinished owned artifacts and can reuse a created remote without touching foreign state.

### Desktop capture and load lifecycle

Desktop capture is an independent `dotfiles sync` phase. With `enabled = false`, it returns before
family detection, artifact lookup, dconf, or a capture commit. Otherwise it normalizes the current
family, inspects the bounded resolved artifact, and preserves it rather than overwriting it when the
prior artifact is invalid or belongs to a different family. An unknown family or missing `dconf`
also skips while retaining the prior artifact. For a compatible family it invokes
`dconf dump <root>` once per sorted effective root, passing the canonical root verbatim and
`LC_ALL=C`. Any failed dump or secret-gate rejection preserves the prior artifact. A newly rendered
artifact passes the same exact-path admission as a fetched artifact and is compared byte-for-byte
with the prior blob; only a difference is eligible for a checked commit.

The phase runs after full-tree validation and reconciliation on the successful online, empty-remote,
and eligible offline paths. It can share the normal checked commit with home-file changes, or create
an artifact-only commit; even a cancelled candidate review does not cancel a changed independent
capture. Offline capture is deferred when the cached remote is ahead, so it never creates a local
artifact commit on an obsolete base.

Desktop load is an independent `dotfiles apply` finalizer behind the existing package gate. A real
apply calls it only after `execute_materialization_plan()` returns successfully, including for an
established no-op, and before the local ref advances and history is recorded. A materialization
failure cannot invoke it. It begins with the enabled gate, then reads the retained full-tree artifact,
strictly parses it, checks the normalized local family, and re-authorizes every parsed root against
the current local effective allowlist. Roots removed locally or crafted outside the allowlist are
reported as suppressed and never reach dconf. Recovery guidance is computed from the actual current
`DesktopSettingsConfig` and validated against that model before display. A jointly applicable
aggregate edit is shown only when the resulting configuration validates; otherwise the report labels
mutually-exclusive alternatives, each individually validated. If a declared root cannot be admitted
by policy, the compatible recovery keeps the local configuration and directs the remote declaration
to a compatible, non-overlapping root instead of suggesting an invalid local edit.

The loader skips, with a reason and re-attempt guidance, for disabled, absent or invalid artifact,
unknown/mismatched family, missing `dconf`, and no reachable session. A session hint from
`DBUS_SESSION_BUS_ADDRESS` or `$XDG_RUNTIME_DIR/bus` is only a preflight: every invocation uses
`LC_ALL=C`, `dconf load -f <root>`, the canonical root verbatim, and the marker-free section body as
text stdin. A narrow C-locale D-Bus transport classifier turns only connection/stale-bus failures
into `no-session`; any other nonzero dconf result is a failed root with stderr retained, and later
roots are not attempted. Previously submitted roots remain reported. `dconf load -f` may skip locked
keys, and a successful exit confirms submission rather than schema validity or final setting
effectiveness; desktop loading is deliberately best effort and does not make file application fail.

`--dry-run` still validates the selected source and renders the ordinary no-clobber plan. For enabled
desktop settings it parses the artifact and previews its family and roots, but makes no dconf call;
for disabled settings it reports the disabled state without artifact lookup or parsing. It writes no
desktop, local, or history state.

### Sync and apply state machines

`sync` first fetches the explicit remote ref. Online, it validates the complete remote tree, refuses
diverged history, conflicts, or tracked-file deletions, materializes a compatible behind ref through
the plan flow above, discovers safe new candidates, and accepts only explicitly reviewed additions.
It then runs the desktop capture phase, checked-commits all eligible local changes and any changed
artifact, persists reviewed ignores/allowlist changes, and pushes if local `main` is ahead. A push
failure leaves the checked local commit as `pending-push` for the next online sync. Offline, it uses
only cached refs: it never materializes remote content or pushes, and it defers a local commit and
desktop capture if the cached remote is ahead.

`apply` enforces the package manifest gate before reading dotfiles. It fetches or uses the cached
remote source, accepts only equal/behind (including bootstrap-behind) ref relations, validates the
full source tree and tracked-path continuity, and builds the same plan. `--dry-run` renders that
deterministic plan with zero writes and previews desktop loading. A real apply materializes every
safe home entry, runs the desktop finalizer, then conditionally advances local `main` only after
materialization succeeds. Both sync and apply record non-reversible action history only after their
successful mutations. Reserved artifact paths are filtered out of history items; successful loads add
the submitted roots and family as metadata on the existing `DOTFILES_APPLY` action.

---

## Desktop Alerts

`popctl alerts` is an optional, self-contained subsystem (`app/popctl/alerts/`) that renders
reminder alerts from a compatible WebSocket alert sink (for example, a nanobot instance) as
COSMIC desktop notifications — both structured calendar events and plain-text standing/ad-hoc
reminders. It is independent of the manifest/sync core.

| Module | Responsibility |
|--------|----------------|
| `protocol.py` | `parse_frame()` decodes a compatible sink's double-encoded WebSocket frame (`{"event":"message","text":"<payload>"}`) and returns `Alert \| PlainAlert \| None`: a structured calendar `Alert` for valid alert JSON, a `PlainAlert` for any other message payload (so a plain reminder is rendered, never dropped), `None` for non-message/malformed frames. An alert-shaped JSON that fails `Alert` validation is still rendered as a `PlainAlert` but logged as probable wire-format drift. |
| `config.py` | `AlertsConfig` (pydantic) loaded from `~/.config/popctl/alerts.toml` via the XDG `core.paths` helpers. |
| `render.py` | Pure: `build_notify_args()` (structured: urgency + `--expire-time` + title/time/location/link), `build_plain_notify_args()` (plain text: first line → summary, rest → body), and `select_sound()` (per-kind → default). The wire kinds `preeve`, `pre`, and `warning` display as `Tomorrow`, `Soon`, and `Now`; an empty plain-text reminder uses `🔔 Reminder`. |
| `notifier.py` | Side effects: `deliver()` dispatches on `Alert` vs `PlainAlert`, then runs `notify-send` + an explicit sound player through `utils.shell.run_command`; resolves the sound as configured → bundled tone → system fallbacks; failures are logged, never swallowed. |
| `daemon.py` | `run()` — a sync `websocket-client` loop: connect → `attach` to the configured `chat_id` → deliver, with exponential reconnect backoff. |
| `sounds/alert.ogg` | Bundled default alert tone (ogg/vorbis), played when no sound is configured. |

The editable configuration and user-service templates are packaged under
`app/popctl/data/templates/`, rather than copied from a repository directory:

| Template | Materialization command | Destination |
|----------|-------------------------|-------------|
| `alerts.toml` | `popctl alerts init-config` | `~/.config/popctl/alerts.toml` |
| `popctl-alerts.service` | `popctl alerts install-service` | `~/.config/systemd/user/popctl-alerts.service` |

`init-config` creates an editable alert configuration from package data and refuses to overwrite it
unless `--force` is supplied. `install-service` resolves the current `popctl` executable into the
unit, then gracefully prints manual systemd commands when a user systemd instance is unavailable.
The CLI also exposes `watch` (run the daemon) and `test` (fire a sample alert without a config, to
validate the desktop notification + sound before deployment).

**Design notes.**
- **Expiry:** An explicit `alerts.toml` `expire_ms` value wins; when omitted, it resolves to
  `30000` ms only on a positively identified non-COSMIC desktop (via `XDG_CURRENT_DESKTOP` /
  `XDG_SESSION_DESKTOP`) and fail-safe to `0` (never expire) on COSMIC, ambiguous, or unknown
  environments — losing a critical reminder is worse than a lingering notification.
- **Sound:** played explicitly through `pw-play`, `paplay`, `canberra-gtk-play`, `ffplay`, `mpv`,
  then `aplay`, rather than via the notification `sound` hint. A bundled tone ships as the
  audible-by-default sound so a missing config never yields a silent alert.
- **Reminder types:** the sink carries two payload shapes on the same channel — structured
  calendar alerts (rich JSON) and plain-text reminders (standing/ad-hoc). `parse_frame` never
  drops a message payload: anything that is not a valid `Alert` renders as a `PlainAlert`, so a
  schema change on the nanobot side degrades to a (visible) plain notification rather than a lost
  alert — and that degradation is logged as drift so it is not silently masked.
- **Delivery:** reconnecting uses exponential backoff. Delivery is best-effort: alerts received
  while disconnected are not replayed.

---

## Data Flow Examples

### Example 1: `popctl sync --auto --yes`

```
sync()
  │
  ├─ _ensure_manifest()
  │    └─ manifest_exists() → True (skip)
  │
  ├─ refresh_manifest_sources()
  │    └─ capture_sources(ALL) → confirm live additions/changes → save only confirmed merge
  │
  ├─ run_source_phase(manifest, ALL)
  │    ├─ selected managers → availability barrier
  │    ├─ capture_platform() + capture_sources(ALL)
  │    ├─ preflight_sources() → platform / suite / public-key checks
  │    ├─ compute_source_diff() → preview + changed-source confirmation
  │    ├─ provision_sources() → managed APT keys/stanzas, scoped Flatpak remotes
  │    └─ sudo apt-get update --error-on=any
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
  │    ├─ diff_to_actions(purge=False, sources=manifest.sources) → [INSTALL×2, REMOVE×3]
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

## On-Disk Format Stability

popctl persists the manifest (including optional source records), four feature configs (advisor,
alerts, backup, dotfiles), and the append-only `history.jsonl` (see the file-locations table). Their
compatibility contract:

- **Stable within a major version.** Fields are only added, never renamed or removed.
  Manifest metadata and selected leaf models (`ManifestMeta`, `SystemConfig`, and
  `PackageEntry`) ignore unknown keys (`extra="ignore"`). The manifest containers
  (`Manifest`, `PackageConfig`, `DomainConfig`, and all `SourcesConfig` models) and `DomainEntry`
  are intentionally strict (`extra="forbid"`): a typo or unrecognized structural manifest field
  must fail loudly instead of being silently dropped. A manifest that uses a new structural field
  therefore requires a popctl upgrade first.
- **Versioning anchor.** The manifest's `[meta]` section is the designated home for a
  `schema_version` sentinel should a breaking change ever become necessary; today no
  format has needed one (deliberate YAGNI — popctl has no database and all formats are
  human-readable TOML/JSONL).
- **Round-trip guarantee.** Save/load round-trips include every source platform, replay, binding,
  and public-key field (`tests/unit/sources/test_models.py`); the manifest is copied into backups.
  `backup.toml` produced by `popctl backup init` is verified through `load_backup_config`, and
  `dotfiles.toml` through `load_dotfiles_config`.
- **Escape hatch.** Every file is plain text under the XDG paths — export/import is a
  file copy; encrypted backups additionally capture them via `popctl backup create`.

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
| Unit tests | `tests/unit/` | 81% aggregate coverage floor |
| Shared fixtures | `tests/unit/conftest.py` | `sample_manifest` with APT + Flatpak packages |
| Source subsystem | `tests/unit/sources/` | Capture, refusal, key trust, diff, preflight, provision, and dry-run paths |

**Key patterns:**
- `typer.testing.CliRunner` for CLI tests
- `tmp_path` fixture for file I/O tests
- `mocker.patch("subprocess.run")` for shell command tests
- Never call real package managers in tests
- Parametrized domain tests: `@pytest.mark.parametrize("domain", ["filesystem", "configs"])`
- Source tests use fixture APT trees and mocked command boundaries; the dedicated branch coverage
  measurement covers `popctl.sources` success, failure, refusal, and dry-run paths.

**Quality gates:** the project runs its quality gates in CI. The aggregate coverage floor and
current baseline are documented above; Pyright and Ruff results are not recorded here.

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
| Dotfiles config | `~/.config/popctl/dotfiles.toml` |
| Dotfiles bare repository | `~/.local/share/popctl/dotfiles.git` |
| Dotfiles state | `~/.local/state/popctl/dotfiles/` (lock, plans, journals, owned Git transport assets) |
| Advisor sessions | `~/.local/state/popctl/sessions/<timestamp>/` (`~/.djinn/sessions/popctl/` when the djinn backend is active) |
| Advisor memory | `~/.local/state/popctl/advisor/memory.md` |
| Default theme | `app/popctl/data/theme.toml` |
