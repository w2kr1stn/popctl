# popctl — Product Specification

**Declarative System Configuration for Pop!_OS**

| Field | Value |
|-------|-------|
| **Version** | 1.0.0-draft |
| **Date** | 2026-01-24 |
| **Target OS** | Pop!_OS 24.04 LTS (COSMIC Desktop) |
| **Author** | Generated with Claude |
| **Status** | Draft Specification |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Goals & Non-Goals](#3-goals--non-goals)
4. [System Architecture](#4-system-architecture)
5. [Pop!_OS 24.04 LTS Baseline](#5-popos-2404-lts-baseline)
6. [Manifest Format Specification](#6-manifest-format-specification)
7. [Component Specifications](#7-component-specifications)
8. [Claude Advisor Integration](#8-claude-advisor-integration)
9. [CLI Command Reference](#9-cli-command-reference)
10. [Security Model](#10-security-model)
11. [Tech Stack](#11-tech-stack)
12. [Implementation Roadmap](#12-implementation-roadmap)
13. [Testing Strategy](#13-testing-strategy)
14. [Appendices](#14-appendices)

---

## 1. Executive Summary

**popctl** is a declarative system configuration tool for Pop!_OS that enables users to define their desired system state in a manifest file and automatically maintain that state over time. It combines deterministic package management with AI-assisted decision-making for unknown packages and configurations.

### Core Value Proposition

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         BEFORE popctl                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  • Packages accumulate over time with no tracking                           │
│  • Config files scattered, drift from desired state                         │
│  • Manual cleanup is tedious and error-prone                                │
│  • No reproducibility across reinstalls                                     │
│  • "What did I install and why?" — unknown                                  │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                         AFTER popctl                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  • Single manifest defines entire system state                              │
│  • Configs versioned and synced from central repo                           │
│  • Automated cleanup removes cruft safely                                   │
│  • Full reproducibility: manifest → identical system                        │
│  • AI assists with classification of unknown packages                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Problem Statement

### 2.1 The Entropy Problem

Every Linux system accumulates "entropy" over time:

| Entropy Type | Example | Impact |
|--------------|---------|--------|
| **Package Sprawl** | Trial apps, forgotten dev tools | Disk usage, update time |
| **Orphaned Dependencies** | Libraries from removed apps | Security risk, confusion |
| **Config Drift** | Manual edits to dotfiles | Inconsistency across machines |
| **Cache Bloat** | npm/pip/flatpak caches | Disk usage (often 10GB+) |
| **Cruft Accumulation** | Empty dirs, old logs, temp files | Clutter, slow filesystem ops |

### 2.2 Current Solutions Fall Short

| Tool | Limitation |
|------|------------|
| `apt autoremove` | Only removes auto-installed packages, misses manually-installed orphans |
| `deborphan` | No longer maintained; only finds libraries, not applications |
| Timeshift | Snapshots entire disk; doesn't help identify what's unwanted |
| Ansible | Enterprise-focused, steep learning curve, overkill for single-user desktop |
| NixOS | Requires switching distributions entirely |
| Chezmoi | Dotfiles only; no package management |

### 2.3 Target User Profile

- **Primary**: Developer/power user running Pop!_OS as daily driver
- **Usage Pattern**: Container-based development (most work in ephemeral containers)
- **Goal**: Ultra-lean host OS with full control over what's installed
- **Technical Level**: Comfortable with CLI, Python, basic system administration

---

## 3. Goals & Non-Goals

### 3.1 Goals

| Priority | Goal | Success Metric |
|----------|------|----------------|
| **P0** | Inventory all installed packages (apt, flatpak, snap) | 100% coverage in single command |
| **P0** | Define desired state in human-readable manifest | TOML format, versionable in Git |
| **P0** | Apply manifest to achieve desired state | Idempotent `apply` command |
| **P0** | Remove unwanted packages and cruft safely | Dry-run by default, undo capability |
| **P1** | Sync dotfiles/configs from central repository | Diff, sync, pull commands |
| **P1** | AI-assisted classification of unknown packages | Claude integration for decision support |
| **P1** | Multi-machine portability | Conditional packages, machine overrides |
| **P2** | Filesystem cruft detection and cleanup | Cache, orphan configs, empty dirs |
| **P2** | GNOME/COSMIC settings management | dconf export/import |

### 3.2 Non-Goals

| Non-Goal | Reason |
|----------|--------|
| Replace package managers | Use apt/flatpak/snap, don't reimplement |
| Full system imaging | Timeshift/Clonezilla handle this better |
| Automated unattended execution | Too risky; always require human confirmation |
| Support for non-Debian distros | Focus on Pop!_OS; may extend later |
| Container orchestration | Separate concern (Docker, Podman) |
| Enterprise multi-user management | Single-user desktop focus |

---

## 4. System Architecture

### 4.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              ARCHITECTURE                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ~/.config/popctl/                     HOST (Pop!_OS)                      │
│   ├── manifest.toml        ◄─────────────────────────────────────────┐      │
│   ├── packages/                                                      │      │
│   ├── configs/             ┌──────────────────┐                      │      │
│   ├── baseline/            │    popctl CLI    │                      │      │
│   └── state/               │   (Python)       │                      │      │
│                            └────────┬─────────┘                      │      │
│                                     │                                │      │
│              ┌──────────────────────┼──────────────────────┐         │      │
│              │                      │                      │         │      │
│              ▼                      ▼                      ▼         │      │
│    ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐   │      │
│    │    SCANNERS     │   │    OPERATORS    │   │     STATE       │   │      │
│    │                 │   │                 │   │                 │   │      │
│    │ • AptScanner    │   │ • AptOperator   │   │ • History       │   │      │
│    │ • FlatpakScan.  │   │ • FlatpakOp.    │   │ • Trash         │   │      │
│    │ • SnapScanner   │   │ • SnapOperator  │   │ • LastScan      │   │      │
│    │ • FilesystemSc. │   │ • ConfigOp.     │   │                 │   │      │
│    │ • DconfScanner  │   │ • FilesystemOp. │   │                 │   │      │
│    └─────────────────┘   └─────────────────┘   └─────────────────┘   │      │
│              │                      │                      │         │      │
│              └──────────────────────┼──────────────────────┘         │      │
│                                     │                                │      │
│                                     ▼                                │      │
│                            ┌─────────────────┐                       │      │
│                            │   SYSTEM APIs   │                       │      │
│                            │ apt, dpkg,      │                       │      │
│                            │ flatpak, snap,  │                       │      │
│                            │ dconf, fs       │                       │      │
│                            └─────────────────┘                       │      │
│                                                                      │      │
│ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│      │
│                                                                      │      │
│   CONTAINER (ai-dev)                                                 │      │
│   ┌─────────────────────────────────────────────────────────────┐    │      │
│   │                    Claude Advisor                           │    │      │
│   │                                                             │    │      │
│   │  • Package Classification                                   │    │      │
│   │  • Filesystem Analysis                                      │    │      │
│   │  • Interactive Decision Support                             │    │      │
│   │                                                             │    │      │
│   │  Input:  /home/dev/popctl-exchange/scan.json                │◄───┘      │
│   │  Output: /home/dev/popctl-exchange/decisions.toml           │───────────┘
│   └─────────────────────────────────────────────────────────────┘           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA FLOW                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  INIT FLOW (one-time setup):                                                │
│                                                                             │
│    popctl init ──► Scan System ──► Generate manifest.toml                   │
│         │              │                    │                               │
│         │              ▼                    ▼                               │
│         │         scan.json ──────► [Container: Claude Advisor]             │
│         │                                   │                               │
│         │                                   ▼                               │
│         │                          Interactive Classification               │
│         │                                   │                               │
│         │                                   ▼                               │
│         └────────────────────────► Final manifest.toml                      │
│                                                                             │
│  APPLY FLOW (regular usage):                                                │
│                                                                             │
│    popctl diff ──► Compare manifest vs. system                              │
│         │                    │                                              │
│         │                    ▼                                              │
│         │         ┌─────────────────────┐                                   │
│         │         │ Changes detected:   │                                   │
│         │         │ • New packages      │                                   │
│         │         │ • Config drift      │                                   │
│         │         │ • Cleanup targets   │                                   │
│         │         └─────────────────────┘                                   │
│         │                    │                                              │
│         ▼                    ▼                                              │
│    popctl apply ──► Execute changes (with confirmation)                     │
│         │                    │                                              │
│         │                    ▼                                              │
│         │         ┌─────────────────────┐                                   │
│         │         │ Actions:            │                                   │
│         │         │ • apt remove X      │                                   │
│         │         │ • flatpak uninstall │                                   │
│         │         │ • sync configs      │                                   │
│         │         └─────────────────────┘                                   │
│         │                    │                                              │
│         ▼                    ▼                                              │
│    History log ◄──────── Record changes for undo                            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.3 Directory Structure

```
~/.config/popctl/
├── manifest.toml              # Main configuration file
├── machine.toml               # Machine-specific overrides (gitignored)
├── packages/
│   ├── apt.toml               # APT package definitions
│   ├── flatpak.toml           # Flatpak application definitions
│   └── snap.toml              # Snap package definitions
├── configs/                   # Managed dotfiles (synced to system)
│   ├── zsh/
│   │   ├── .zshrc
│   │   └── .zshenv
│   ├── nvim/
│   │   └── init.lua
│   ├── git/
│   │   ├── .gitconfig
│   │   └── .gitignore_global
│   └── cosmic/
│       └── settings.ron       # COSMIC desktop settings
├── baseline/
│   └── pop-os-24.04.toml      # Reference: default Pop!_OS installation
├── rules/
│   ├── filesystem.toml        # Cleanup rules for filesystem
│   └── cruft-patterns.toml    # Known cruft patterns
└── state/                     # Runtime state (gitignored)
    ├── last-scan.json         # Most recent system scan
    ├── history.jsonl          # Change history (append-only log)
    └── trash/                 # Deleted configs (for undo)
        └── 2026-01-24_153000/
            └── .config/old-app/
```

---

## 5. Pop!_OS 24.04 LTS Baseline

### 5.1 System Overview

Pop!_OS 24.04 LTS represents a major shift from GNOME to the new COSMIC desktop environment built in Rust.

| Component | Version | Notes |
|-----------|---------|-------|
| **Base** | Ubuntu 24.04 LTS | Full access to Ubuntu repos |
| **Kernel** | 6.17.x | Latest HWE kernel |
| **Desktop** | COSMIC Epoch 1 | Rust-based, Wayland-native |
| **Display Server** | Wayland | X11 available as fallback |
| **Graphics** | Mesa 25.1 / NVIDIA 580 | Hybrid graphics support |
| **Init** | systemd | Standard Linux init |
| **Package Formats** | APT, Flatpak, (Snap optional) | Flathub enabled by default |

### 5.2 Default COSMIC Applications

These applications are installed by default and should be preserved unless explicitly removed:

| Application | Package | Purpose |
|-------------|---------|---------|
| **COSMIC Files** | `cosmic-files` | File manager |
| **COSMIC Terminal** | `cosmic-term` | Terminal emulator |
| **COSMIC Text Editor** | `cosmic-edit` | Text editor |
| **COSMIC Store** | `cosmic-store` | App store (apt + flatpak) |
| **COSMIC Settings** | `cosmic-settings` | System settings |
| **COSMIC Media Player** | `cosmic-player` | Media playback |
| **Firefox** | `firefox` | Web browser |

### 5.3 Default Preinstalled Software

Based on fresh Pop!_OS 24.04 installation:

| Category | Packages |
|----------|----------|
| **Productivity** | LibreOffice suite, Thunderbird (email) |
| **Utilities** | Popsicle (USB creator), GNOME Disk Utility, Document Scanner |
| **System** | GNOME System Monitor, Firmware Manager |
| **Drivers** | System76 drivers, NVIDIA drivers (if applicable) |

### 5.4 Essential System Packages (Never Remove)

```toml
# Patterns that should NEVER be removed
[baseline.protected]
patterns = [
    "linux-*",              # Kernel packages
    "systemd*",             # Init system
    "pop-*",                # Pop!_OS specific packages
    "cosmic-*",             # COSMIC desktop components
    "system76-*",           # System76 hardware support
    "plymouth*",            # Boot splash
    "grub-*",               # Bootloader
    "ubuntu-*",             # Ubuntu base packages
    "apt*",                 # Package management
    "dpkg*",                # Package management
    "libc*",                # C library
    "libstdc++*",           # C++ library
    "dbus*",                # Message bus
    "networkmanager*",      # Network management
    "pulseaudio*",          # Audio (if used)
    "pipewire*",            # Audio (COSMIC default)
]
```

### 5.5 Default Flatpak Remotes

```
┌────────────────────────────────────────────────┐
│ Flatpak Remotes (enabled by default)           │
├────────────────────────────────────────────────┤
│ • flathub (https://flathub.org/repo/)          │
│ • system76-flatpak (System76 apps)             │
└────────────────────────────────────────────────┘
```

### 5.6 Filesystem Baseline

Standard XDG directories created by default:

| Directory | Purpose | popctl Behavior |
|-----------|---------|-----------------|
| `~/.config/` | User configuration | Managed (selective) |
| `~/.local/share/` | User data | Preserve, scan for orphans |
| `~/.cache/` | Caches | Auto-clean eligible |
| `~/Documents/` | User documents | Never touch |
| `~/Downloads/` | Downloads | Never touch |
| `~/Pictures/` | Pictures | Never touch |
| `~/Videos/` | Videos | Never touch |
| `~/Music/` | Music | Never touch |

---

## 6. Manifest Format Specification

### 6.1 manifest.toml (Main File)

```toml
# =============================================================================
# popctl Manifest — Declarative System Configuration
# =============================================================================
# Version: 1.0
# Documentation: https://github.com/user/popctl
# =============================================================================

[manifest]
version = "1.0"
created = "2026-01-24T15:30:00Z"
updated = "2026-01-24T18:45:00Z"

# -----------------------------------------------------------------------------
# System Identification
# -----------------------------------------------------------------------------
[system]
name = "pop-workstation"              # Human-friendly name
base = "pop-os-24.04"                 # Baseline reference
description = "Primary development workstation"

# Machine ID (auto-generated, used for multi-machine sync)
machine_id = "desktop-home-abc123"

# -----------------------------------------------------------------------------
# Package Management
# -----------------------------------------------------------------------------
# Detailed package lists are in separate files for clarity
[packages]
apt = { include = "packages/apt.toml" }
flatpak = { include = "packages/flatpak.toml" }
snap = { include = "packages/snap.toml" }

# -----------------------------------------------------------------------------
# Configuration Management
# -----------------------------------------------------------------------------
[configs]
# Directory containing managed dotfiles
source_dir = "configs"

# Sync strategy
#   "copy"  = Copy files (safer, explicit sync required)
#   "link"  = Symlinks (changes immediately active)
method = "copy"

# Conflict resolution
#   "ask"      = Prompt user
#   "manifest" = Manifest always wins
#   "system"   = System file wins (pull behavior)
on_conflict = "ask"

# File mappings: source (relative to configs/) → destination
[configs.mappings]
"zsh/.zshrc" = "~/.zshrc"
"zsh/.zshenv" = "~/.zshenv"
"nvim" = "~/.config/nvim"
"git/.gitconfig" = "~/.gitconfig"
"git/.gitignore_global" = "~/.gitignore_global"
"alacritty" = "~/.config/alacritty"

# COSMIC/dconf settings (special handling)
# "cosmic/settings.ron" = { type = "cosmic", path = "~/.config/cosmic/" }

# -----------------------------------------------------------------------------
# Filesystem Cleanup
# -----------------------------------------------------------------------------
[filesystem]
rules = { include = "rules/filesystem.toml" }

# -----------------------------------------------------------------------------
# Interaction Settings
# -----------------------------------------------------------------------------
[interaction]
# Default mode: "hybrid", "auto", "paranoid"
default_mode = "hybrid"

# Confidence thresholds for hybrid mode
[interaction.confidence]
auto_keep_threshold = 0.95      # Above this: auto-keep
auto_remove_threshold = 0.95    # Above this: auto-remove
# Between thresholds: ask user

# -----------------------------------------------------------------------------
# History & Rollback
# -----------------------------------------------------------------------------
[history]
max_undo_steps = 50             # How many operations to keep
trash_retention_days = 30       # How long to keep deleted configs

# -----------------------------------------------------------------------------
# Claude Advisor Configuration
# -----------------------------------------------------------------------------
[advisor]
# Model for package classification
model = "claude-sonnet-4-20250514"

# Batch size for API calls
batch_size = 20

# Cache Claude responses
cache_enabled = true
cache_ttl_days = 90
```

### 6.2 packages/apt.toml

```toml
# =============================================================================
# APT Packages
# =============================================================================

[apt]
# Auto-remove orphaned packages during cleanup
auto_remove_orphans = true

# Auto-clean apt cache
auto_clean_cache = true

# -----------------------------------------------------------------------------
# Explicitly Desired Packages (beyond baseline)
# -----------------------------------------------------------------------------
[apt.keep]
# Shell & Terminal
shell = [
    "zsh",
    "zsh-autosuggestions",
    "zsh-syntax-highlighting",
]

# Editors
editors = [
    "neovim",
]

# CLI Tools
cli = [
    "ripgrep",
    "fd-find",
    "bat",
    "eza",
    "fzf",
    "jq",
    "yq",
    "htop",
    "btop",
    "tree",
    "tmux",
    "curl",
    "wget",
    "httpie",
]

# Development
development = [
    "build-essential",
    "git",
    "git-lfs",
]

# Container Tools
containers = [
    "docker-ce",
    "docker-ce-cli",
    "containerd.io",
    "docker-compose-plugin",
]

# -----------------------------------------------------------------------------
# Explicitly Unwanted Packages
# -----------------------------------------------------------------------------
[apt.remove]
packages = [
    "nano",           # Replaced by neovim
    "vim-tiny",       # Replaced by neovim
    "apport",         # Crash reporter (privacy)
    "whoopsie",       # Ubuntu error tracking
    "popularity-contest",  # Package statistics
]

# -----------------------------------------------------------------------------
# Conditional Packages (hardware-dependent)
# -----------------------------------------------------------------------------
[apt.conditional.nvidia]
detect = "lspci | grep -qi nvidia"
packages = [
    "nvidia-driver-550",
    "nvtop",
]

[apt.conditional.laptop]
detect = "test -d /sys/class/power_supply/BAT0"
packages = [
    "tlp",
    "tlp-rdw",
    "powertop",
]

[apt.conditional.bluetooth]
detect = "test -d /sys/class/bluetooth"
packages = [
    "bluez",
    "blueman",
]

# -----------------------------------------------------------------------------
# Machine-Specific Packages
# -----------------------------------------------------------------------------
[apt.machines.desktop-home]
extra = [
    "steam-installer",
]

[apt.machines.laptop-work]
extra = [
    "openconnect",
    "network-manager-openconnect",
]

# -----------------------------------------------------------------------------
# Never Remove (even if orphaned)
# -----------------------------------------------------------------------------
[apt.protected]
patterns = [
    "linux-*",
    "cosmic-*",
    "pop-*",
    "systemd*",
    "lib*",             # Libraries managed by apt
]
```

### 6.3 packages/flatpak.toml

```toml
# =============================================================================
# Flatpak Applications
# =============================================================================

[flatpak]
# Flatpak remotes to use
remotes = ["flathub", "system76-flatpak"]

# Auto-remove unused runtimes
auto_remove_unused = true

# -----------------------------------------------------------------------------
# Desired Applications
# -----------------------------------------------------------------------------
[flatpak.keep]
productivity = [
    "org.mozilla.firefox",
    "md.obsidian.Obsidian",
]

communication = [
    "com.discordapp.Discord",
    "com.slack.Slack",
]

media = [
    "com.spotify.Client",
    "org.videolan.VLC",
]

development = [
    "com.getpostman.Postman",
    "io.dbeaver.DBeaverCommunity",
]

utilities = [
    "com.github.tchx84.Flatseal",    # Flatpak permissions manager
    "org.flameshot.Flameshot",
]

# -----------------------------------------------------------------------------
# Explicitly Remove
# -----------------------------------------------------------------------------
[flatpak.remove]
apps = [
    # Apps you've tried but don't want
]

# -----------------------------------------------------------------------------
# Machine-Specific
# -----------------------------------------------------------------------------
[flatpak.machines.desktop-home]
extra = [
    "com.valvesoftware.Steam",
]
```

### 6.4 packages/snap.toml

```toml
# =============================================================================
# Snap Packages
# =============================================================================

[snap]
# Disable snap entirely?
# true  = Remove snapd and all snaps
# false = Manage snaps selectively
enabled = false

# If enabled = true, specify which snaps to keep
[snap.keep]
apps = [
    # "lxd",    # Only if using LXD containers
]

# Migration: Install flatpak equivalent when removing snap
[snap.migration]
auto_migrate = true
mappings = [
    { snap = "firefox", flatpak = "org.mozilla.firefox" },
    { snap = "spotify", flatpak = "com.spotify.Client" },
    { snap = "code", flatpak = "com.visualstudio.code" },
]
```

### 6.5 rules/filesystem.toml

```toml
# =============================================================================
# Filesystem Cleanup Rules
# =============================================================================

[cleanup]
# Safety: Always dry-run first
dry_run_default = true

# Confirm if deleting more than this much data
confirm_threshold_mb = 1000

# -----------------------------------------------------------------------------
# AUTO-CLEAN: Remove without asking
# -----------------------------------------------------------------------------
[cleanup.auto]
# Cache directories
caches = [
    "~/.cache/**",
    "~/.local/share/Trash/**",
    "~/.thumbnails/**",
    "~/.npm/_cacache/**",
    "~/.pnpm-store/**",
    "~/.yarn/cache/**",
    "~/.cargo/registry/cache/**",
    "~/.rustup/tmp/**",
    "~/.local/share/flatpak/repo/tmp/**",
]

# Build artifacts (only in ~/projects or similar)
build_artifacts = [
    "~/projects/**/node_modules/**",
    "~/projects/**/__pycache__/**",
    "~/projects/**/*.pyc",
    "~/projects/**/.pytest_cache/**",
    "~/projects/**/.mypy_cache/**",
    "~/projects/**/.ruff_cache/**",
    "~/projects/**/target/debug/**",
    "~/projects/**/dist/**",
    "~/projects/**/*.egg-info/**",
]

# Log files older than threshold
[cleanup.auto.logs]
patterns = [
    "~/.local/share/**/*.log",
    "~/.config/**/*.log",
]
max_age_days = 30

# Old kernels
[cleanup.auto.kernels]
enabled = true
keep_count = 2    # Keep latest 2 kernels

# -----------------------------------------------------------------------------
# REVIEW: Send to Claude for classification
# -----------------------------------------------------------------------------
[cleanup.review]
# Config directories for apps that aren't installed
orphan_configs = true

# Large directories that might be forgotten
large_dirs_threshold_mb = 500

# Directories not accessed in N days
stale_dirs_days = 180

# -----------------------------------------------------------------------------
# PRESERVE: Never touch
# -----------------------------------------------------------------------------
[cleanup.preserve]
paths = [
    "~/.ssh/**",
    "~/.gnupg/**",
    "~/.password-store/**",
    "~/.local/share/keyrings/**",
    "~/Documents/**",
    "~/Pictures/**",
    "~/Videos/**",
    "~/Music/**",
    "~/projects/**",            # Your code (except build artifacts)
    "~/.config/popctl/**",      # Don't delete ourselves
]

# Large but intentionally kept
large_but_keep = [
    "~/.local/share/Steam/**",
    "~/.var/app/com.valvesoftware.Steam/**",
    "~/.local/share/containers/**",    # Podman
]

# -----------------------------------------------------------------------------
# EXTERNAL DRIVES (opt-in)
# -----------------------------------------------------------------------------
[cleanup.external]
enabled = false

# Only scan these paths (if enabled)
paths = [
    # "/mnt/data/projects",
]

[cleanup.external.rules]
node_modules = true
pycache = true
git_gc = true           # Run git gc --aggressive
empty_dirs = true
```

### 6.6 machine.toml (Local Overrides)

```toml
# =============================================================================
# Machine-Specific Configuration
# =============================================================================
# This file is NOT committed to Git.
# It contains machine-specific overrides.
# =============================================================================

[machine]
id = "desktop-home-abc123"
hostname = "pop-desktop"
description = "Home desktop with NVIDIA RTX 3080"

# Hardware detection overrides
[machine.hardware]
has_nvidia = true
has_battery = false
has_bluetooth = true

# Local path overrides
[machine.paths]
projects = "~/code"

# Additional paths to preserve (only on this machine)
extra_preserve = [
    "~/VMs/**",
]

# API configuration (if not using environment variables)
# [machine.api]
# anthropic_api_key = "sk-..."    # Better: use ANTHROPIC_API_KEY env var
```

---

## 7. Component Specifications

### 7.1 Scanner Interface

All scanners implement a common interface for consistency:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Iterator

class PackageSource(Enum):
    APT = "apt"
    FLATPAK = "flatpak"
    SNAP = "snap"

class PackageStatus(Enum):
    INSTALLED = "installed"
    AUTO_INSTALLED = "auto"      # Installed as dependency
    MANUAL = "manual"            # Explicitly installed

@dataclass
class ScannedPackage:
    """Represents a package found during system scan."""
    name: str
    source: PackageSource
    version: str
    status: PackageStatus
    description: str | None = None
    size_bytes: int | None = None
    install_date: str | None = None
    
    # Classification (populated by Claude Advisor)
    classification: str | None = None    # "keep", "remove", "unknown"
    confidence: float | None = None      # 0.0 - 1.0
    reason: str | None = None
    category: str | None = None          # "system", "development", etc.

class Scanner(ABC):
    """Abstract base class for all package scanners."""
    
    @property
    @abstractmethod
    def source(self) -> PackageSource:
        """Which package source this scanner handles."""
        pass
    
    @abstractmethod
    def scan(self) -> Iterator[ScannedPackage]:
        """Yield all installed packages from this source."""
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """Check if this package manager is available on the system."""
        pass
    
    def scan_manual_only(self) -> Iterator[ScannedPackage]:
        """Yield only manually-installed packages."""
        for pkg in self.scan():
            if pkg.status == PackageStatus.MANUAL:
                yield pkg
```

### 7.2 APT Scanner Implementation

```python
class AptScanner(Scanner):
    """Scanner for APT/dpkg packages."""
    
    @property
    def source(self) -> PackageSource:
        return PackageSource.APT
    
    def is_available(self) -> bool:
        return shutil.which("apt") is not None
    
    def scan(self) -> Iterator[ScannedPackage]:
        # Get all installed packages
        result = subprocess.run(
            ["dpkg-query", "-W", "-f",
             '${Package}\t${Version}\t${Installed-Size}\t${binary:Summary}\n'],
            capture_output=True, text=True
        )
        
        # Get auto-installed packages
        auto_result = subprocess.run(
            ["apt-mark", "showauto"],
            capture_output=True, text=True
        )
        auto_packages = set(auto_result.stdout.strip().split('\n'))
        
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) < 4:
                continue
                
            name, version, size, description = parts[0], parts[1], parts[2], parts[3]
            status = (PackageStatus.AUTO_INSTALLED
                     if name in auto_packages
                     else PackageStatus.MANUAL)
            
            yield ScannedPackage(
                name=name,
                source=PackageSource.APT,
                version=version,
                status=status,
                description=description,
                size_bytes=int(size) * 1024 if size.isdigit() else None,
            )
```

### 7.3 Flatpak Scanner Implementation

```python
class FlatpakScanner(Scanner):
    """Scanner for Flatpak applications and runtimes."""
    
    @property
    def source(self) -> PackageSource:
        return PackageSource.FLATPAK
    
    def is_available(self) -> bool:
        return shutil.which("flatpak") is not None
    
    def scan(self) -> Iterator[ScannedPackage]:
        # List all installed flatpaks (apps and runtimes)
        result = subprocess.run(
            ["flatpak", "list", "--columns=application,version,size,description"],
            capture_output=True, text=True
        )
        
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) < 4:
                continue
            
            app_id, version, size_str, description = parts
            
            # Parse size (e.g., "1.2 GB" -> bytes)
            size_bytes = self._parse_size(size_str)
            
            yield ScannedPackage(
                name=app_id,
                source=PackageSource.FLATPAK,
                version=version,
                status=PackageStatus.MANUAL,  # Flatpaks are always manual
                description=description,
                size_bytes=size_bytes,
            )
    
    def _parse_size(self, size_str: str) -> int | None:
        """Parse size string like '1.2 GB' to bytes."""
        try:
            parts = size_str.strip().split()
            if len(parts) != 2:
                return None
            value = float(parts[0])
            unit = parts[1].upper()
            multipliers = {'B': 1, 'KB': 1024, 'MB': 1024**2, 'GB': 1024**3}
            return int(value * multipliers.get(unit, 1))
        except (ValueError, KeyError):
            return None
```

### 7.4 Filesystem Scanner Implementation

```python
@dataclass
class FilesystemEntry:
    """Represents a file or directory found during filesystem scan."""
    path: str
    entry_type: str          # "file", "directory"
    size_bytes: int
    last_accessed: datetime
    last_modified: datetime
    classification: str | None = None  # "cache", "orphan_config", "cruft", etc.
    confidence: float | None = None
    associated_package: str | None = None

class FilesystemScanner:
    """Scanner for filesystem cruft and orphaned configurations."""
    
    # Known safe-to-remove patterns
    KNOWN_CRUFT = [
        ("~/.cache/**", "cache"),
        ("~/.local/share/Trash/**", "trash"),
        ("**/node_modules/**", "build_artifact"),
        ("**/__pycache__/**", "build_artifact"),
        ("~/.npm/_cacache/**", "cache"),
        ("~/.thumbnails/**", "cache"),
    ]
    
    # Never touch these
    SACRED_PATHS = [
        "~/.ssh/**",
        "~/.gnupg/**",
        "~/.password-store/**",
        "~/.local/share/keyrings/**",
    ]
    
    def __init__(self, installed_packages: set[str]):
        """
        Args:
            installed_packages: Set of installed package names for orphan detection
        """
        self.installed_packages = installed_packages
    
    def scan_for_cruft(self) -> dict[str, list[FilesystemEntry]]:
        """
        Scan filesystem for cleanup candidates.
        
        Returns:
            Dict with keys: "auto_removable", "needs_review", "orphan_configs"
        """
        results = {
            "auto_removable": [],
            "needs_review": [],
            "orphan_configs": [],
        }
        
        # Scan known cruft patterns
        for pattern, cruft_type in self.KNOWN_CRUFT:
            expanded = Path(pattern.replace("~", str(Path.home())))
            for path in expanded.parent.glob(expanded.name):
                if self._is_sacred(path):
                    continue
                entry = self._create_entry(path, cruft_type)
                results["auto_removable"].append(entry)
        
        # Scan ~/.config for orphaned configs
        config_dir = Path.home() / ".config"
        for subdir in config_dir.iterdir():
            if subdir.is_dir() and not self._has_installed_package(subdir.name):
                entry = self._create_entry(subdir, "orphan_config")
                results["orphan_configs"].append(entry)
        
        # Scan for large directories
        for entry in results["orphan_configs"]:
            if entry.size_bytes > 500 * 1024 * 1024:  # 500 MB
                results["needs_review"].append(entry)
        
        return results
    
    def _is_sacred(self, path: Path) -> bool:
        """Check if path is in sacred (never-touch) list."""
        path_str = str(path)
        for pattern in self.SACRED_PATHS:
            if fnmatch.fnmatch(path_str, pattern.replace("~", str(Path.home()))):
                return True
        return False
    
    def _has_installed_package(self, name: str) -> bool:
        """Check if a package with similar name is installed."""
        # Simple heuristic: check if name matches any installed package
        name_lower = name.lower().replace("-", "").replace("_", "")
        for pkg in self.installed_packages:
            pkg_lower = pkg.lower().replace("-", "").replace("_", "")
            if name_lower in pkg_lower or pkg_lower in name_lower:
                return True
        return False
    
    def _create_entry(self, path: Path, classification: str) -> FilesystemEntry:
        """Create FilesystemEntry from path."""
        stat = path.stat()
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.is_dir() else stat.st_size
        return FilesystemEntry(
            path=str(path),
            entry_type="directory" if path.is_dir() else "file",
            size_bytes=size,
            last_accessed=datetime.fromtimestamp(stat.st_atime),
            last_modified=datetime.fromtimestamp(stat.st_mtime),
            classification=classification,
            confidence=0.9 if classification in ("cache", "trash") else 0.5,
        )
```

### 7.5 Operator Interface

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

class ActionType(Enum):
    INSTALL = "install"
    REMOVE = "remove"
    PURGE = "purge"         # Remove + delete config

@dataclass
class Action:
    """Represents a single operation to perform."""
    action_type: ActionType
    package: str
    source: PackageSource
    reason: str | None = None

@dataclass
class ActionResult:
    """Result of executing an action."""
    action: Action
    success: bool
    message: str | None = None
    error: str | None = None

class Operator(ABC):
    """Abstract base class for package operations."""
    
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
    
    @property
    @abstractmethod
    def source(self) -> PackageSource:
        pass
    
    @abstractmethod
    def install(self, packages: list[str]) -> list[ActionResult]:
        pass
    
    @abstractmethod
    def remove(self, packages: list[str], purge: bool = False) -> list[ActionResult]:
        pass
    
    def execute(self, actions: list[Action]) -> list[ActionResult]:
        """Execute a batch of actions."""
        results = []
        
        installs = [a.package for a in actions if a.action_type == ActionType.INSTALL]
        removes = [a.package for a in actions if a.action_type == ActionType.REMOVE]
        purges = [a.package for a in actions if a.action_type == ActionType.PURGE]
        
        if installs:
            results.extend(self.install(installs))
        if removes:
            results.extend(self.remove(removes, purge=False))
        if purges:
            results.extend(self.remove(purges, purge=True))
        
        return results
```

### 7.6 State Management

```python
@dataclass
class HistoryEntry:
    """Single entry in the change history."""
    timestamp: datetime
    action_type: str           # "install", "remove", "config_sync", etc.
    items: list[str]           # Package names or file paths
    source: str                # "apt", "flatpak", "filesystem", etc.
    reversible: bool           # Can this action be undone?
    metadata: dict | None = None

class StateManager:
    """Manages persistent state: history, trash, scan results."""
    
    def __init__(self, base_path: Path | None = None):
        self.base_path = base_path or Path.home() / ".config" / "popctl" / "state"
        self.history_file = self.base_path / "history.jsonl"
        self.trash_dir = self.base_path / "trash"
        self.scan_file = self.base_path / "last-scan.json"
    
    def record_action(self, entry: HistoryEntry) -> None:
        """Append action to history log."""
        self.base_path.mkdir(parents=True, exist_ok=True)
        with open(self.history_file, "a") as f:
            f.write(json.dumps(asdict(entry), default=str) + "\n")
    
    def get_history(self, limit: int = 50) -> list[HistoryEntry]:
        """Get recent history entries."""
        if not self.history_file.exists():
            return []
        
        entries = []
        with open(self.history_file) as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    data["timestamp"] = datetime.fromisoformat(data["timestamp"])
                    entries.append(HistoryEntry(**data))
        
        return entries[-limit:]
    
    def trash_configs(self, paths: list[Path]) -> str:
        """Move config files to trash for potential undo."""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        trash_subdir = self.trash_dir / timestamp
        trash_subdir.mkdir(parents=True, exist_ok=True)
        
        for path in paths:
            if path.exists():
                dest = trash_subdir / path.relative_to(Path.home())
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest))
        
        return str(trash_subdir)
    
    def restore_from_trash(self, trash_path: str) -> list[Path]:
        """Restore files from trash to original locations."""
        trash_dir = Path(trash_path)
        restored = []
        
        for file in trash_dir.rglob("*"):
            if file.is_file():
                # Reconstruct original path
                relative = file.relative_to(trash_dir)
                original = Path.home() / relative
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(file), str(original))
                restored.append(original)
        
        # Clean up empty trash directory
        shutil.rmtree(trash_dir)
        return restored
    
    def save_scan(self, packages: list[ScannedPackage]) -> None:
        """Save scan results for later comparison."""
        data = {
            "timestamp": datetime.now().isoformat(),
            "packages": [asdict(p) for p in packages],
        }
        self.scan_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.scan_file, "w") as f:
            json.dump(data, f, indent=2)
```

---

## 8. Claude Advisor Integration

### 8.1 Overview

The Claude Advisor provides AI-assisted decision support for:
- Classifying unknown packages
- Identifying orphaned configurations
- Recommending cleanup actions

It runs **inside the container** (ai-dev) for security, communicating via file exchange.

### 8.2 Communication Protocol

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    HOST ◄──────────────────────► CONTAINER                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   Host writes:                              Container reads:                │
│   /tmp/popctl-exchange/scan.json    ──────► ~/popctl-exchange/scan.json     │
│                                                                             │
│   Host reads:                               Container writes:               │
│   /tmp/popctl-exchange/decisions.toml ◄──── ~/popctl-exchange/decisions.toml│
│                                                                             │
│   Volume mount in docker-compose:                                           │
│   - /tmp/popctl-exchange:/home/dev/popctl-exchange                          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.3 scan.json Format

```json
{
  "scan_date": "2026-01-24T15:30:00Z",
  "system": {
    "hostname": "pop-desktop",
    "os": "Pop!_OS 24.04 LTS",
    "machine_id": "desktop-home-abc123"
  },
  "packages": [
    {
      "name": "libvdpau-va-gl1",
      "source": "apt",
      "version": "0.4.2-1build1",
      "status": "auto",
      "description": "VDPAU driver with VA-API/OpenGL backend",
      "size_bytes": 45056
    },
    {
      "name": "com.example.UnknownApp",
      "source": "flatpak",
      "version": "1.2.3",
      "status": "manual",
      "description": "Some application",
      "size_bytes": 104857600
    }
  ],
  "filesystem": {
    "orphan_configs": [
      {
        "path": "~/.config/unknown-app",
        "size_bytes": 12582912,
        "last_accessed": "2024-06-15T10:00:00Z"
      }
    ],
    "large_dirs": [
      {
        "path": "~/.local/share/SomeApp",
        "size_bytes": 5368709120
      }
    ]
  },
  "installed_apps": ["firefox", "steam", "discord", "spotify"]
}
```

### 8.4 decisions.toml Format

```toml
# Generated by Claude Advisor
# Date: 2026-01-24T16:00:00Z

[packages.apt]
keep = [
    { name = "libvdpau-va-gl1", reason = "GPU video acceleration library, needed for hardware video decoding", confidence = 0.95 },
]
remove = [
    { name = "some-obsolete-package", reason = "Obsolete library no longer maintained", confidence = 0.92 },
]
ask_user = [
    { name = "ambiguous-package", reason = "Could be either development tool or system dependency", confidence = 0.45 },
]

[packages.flatpak]
keep = []
remove = []
ask_user = [
    { name = "com.example.UnknownApp", reason = "Cannot determine purpose from name", confidence = 0.30 },
]

[filesystem]
safe_to_remove = [
    { path = "~/.config/unknown-app", reason = "Config for app that is not installed", confidence = 0.88 },
]
preserve = [
    { path = "~/.local/share/SomeApp", reason = "Large data directory, may contain user data", confidence = 0.75 },
]
ask_user = []
```

### 8.5 Classification Prompt Template

```python
CLASSIFICATION_PROMPT = """
You are a Linux system administration expert helping to classify packages on a Pop!_OS 24.04 system.

## Context
- OS: Pop!_OS 24.04 LTS with COSMIC desktop
- User profile: Developer who uses containers for most work
- Goal: Identify packages that can be safely removed vs. must be kept

## Installed Applications
The user has these applications installed (for context):
{installed_apps}

## Packages to Classify
{packages}

## Classification Rules
1. **KEEP** (confidence > 0.9):
   - System-critical packages (kernel, systemd, drivers)
   - Libraries actively used by installed applications
   - Hardware support (GPU, audio, network drivers)
   - Desktop environment components (COSMIC, GNOME libraries)

2. **REMOVE** (confidence > 0.9):
   - Packages for uninstalled applications
   - Obsolete/deprecated packages
   - Known bloatware or telemetry

3. **ASK_USER** (confidence < 0.9):
   - Packages with unclear purpose
   - Development tools (user might need them)
   - Optional features

## Output Format
Return a JSON object with this structure:
```json
{{
  "classifications": [
    {{
      "package": "package-name",
      "classification": "keep" | "remove" | "ask",
      "confidence": 0.0-1.0,
      "reason": "Brief explanation",
      "category": "system" | "desktop" | "development" | "media" | "network" | "other"
    }}
  ]
}}
```

Classify each package. Be conservative - when in doubt, recommend keeping.
"""
```

### 8.6 Advisor Implementation

```python
from anthropic import Anthropic

class PackageClassifier:
    """Use Claude to classify unknown packages."""
    
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-20250514",
        cache_dir: Path | None = None,
    ):
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.cache_dir = cache_dir or Path.home() / ".cache" / "popctl" / "advisor"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def classify_batch(
        self,
        packages: list[ScannedPackage],
        installed_apps: list[str],
    ) -> list[ClassificationResult]:
        """Classify a batch of packages using Claude."""
        
        # Check cache first
        results = []
        uncached = []
        
        for pkg in packages:
            cached = self._get_cached(pkg)
            if cached:
                results.append(cached)
            else:
                uncached.append(pkg)
        
        if not uncached:
            return results
        
        # Build prompt
        pkg_list = "\n".join([
            f"- {p.name}: {p.description or 'No description'} "
            f"(source: {p.source.value}, status: {p.status.value}, "
            f"size: {self._format_size(p.size_bytes)})"
            for p in uncached
        ])
        
        prompt = CLASSIFICATION_PROMPT.format(
            installed_apps=", ".join(installed_apps),
            packages=pkg_list,
        )
        
        # Call Claude
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        
        # Parse response
        new_results = self._parse_response(response.content[0].text)
        
        # Cache results
        for result in new_results:
            self._cache_result(result)
        
        return results + new_results
    
    def _get_cached(self, package: ScannedPackage) -> ClassificationResult | None:
        """Check if we have a cached classification."""
        cache_file = self.cache_dir / f"{package.source.value}_{package.name}.json"
        if cache_file.exists():
            # Check age
            age = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)
            if age.days < 90:  # Cache valid for 90 days
                with open(cache_file) as f:
                    data = json.load(f)
                return ClassificationResult(**data)
        return None
    
    def _cache_result(self, result: ClassificationResult) -> None:
        """Cache a classification result."""
        cache_file = self.cache_dir / f"{result.source}_{result.package}.json"
        with open(cache_file, "w") as f:
            json.dump(asdict(result), f)
    
    def _parse_response(self, text: str) -> list[ClassificationResult]:
        """Parse Claude's JSON response."""
        # Extract JSON from response
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        
        data = json.loads(text)
        
        return [
            ClassificationResult(
                package=item["package"],
                classification=item["classification"],
                confidence=item["confidence"],
                reason=item["reason"],
                category=item.get("category"),
            )
            for item in data.get("classifications", [])
        ]
    
    def _format_size(self, size_bytes: int | None) -> str:
        """Format size in human-readable form."""
        if size_bytes is None:
            return "unknown"
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"
```

### 8.7 Container Integration

Add to `docker-compose.yml`:

```yaml
services:
  dev:
    volumes:
      # Existing volumes...
      
      # popctl exchange directory
      - /tmp/popctl-exchange:/home/dev/popctl-exchange
      
      # Read-only access to popctl config (for reference)
      - ~/.config/popctl:/home/dev/.config/popctl:ro
    
    environment:
      POPCTL_EXCHANGE: /home/dev/popctl-exchange
```

---

## 9. CLI Command Reference

### 9.1 Command Overview

```
popctl - Declarative system configuration for Pop!_OS

Usage: popctl <command> [options]

Commands:
  init          Initialize popctl and create initial manifest
  scan          Scan system for installed packages
  diff          Show differences between manifest and system
  apply         Apply manifest to system (install/remove packages)
  clean         Remove cruft (caches, orphans, etc.)
  config        Manage configuration files
  history       Show change history
  undo          Undo recent changes
  advisor       Run Claude-assisted classification

Global Options:
  --config PATH     Path to manifest directory (default: ~/.config/popctl)
  --dry-run         Show what would happen without making changes
  --verbose, -v     Increase output verbosity
  --quiet, -q       Suppress non-essential output
  --help, -h        Show help for command
```

### 9.2 Command Details

#### `popctl init`

```
Initialize popctl configuration

Usage: popctl init [options]

This command:
  1. Creates ~/.config/popctl/ directory structure
  2. Scans system for all installed packages
  3. Generates initial manifest.toml
  4. Creates Pop!_OS 24.04 baseline reference

Options:
  --force           Overwrite existing configuration
  --minimal         Create minimal manifest (only custom packages)
  --export PATH     Also export scan to JSON for advisor

Examples:
  popctl init
  popctl init --export /tmp/initial-scan.json
```

#### `popctl scan`

```
Scan system for installed packages and filesystem state

Usage: popctl scan [options]

Options:
  --source SOURCE   Only scan specific source (apt, flatpak, snap, filesystem)
  --manual-only     Only show manually installed packages
  --export PATH     Export results to JSON file
  --format FORMAT   Output format: table, json, yaml (default: table)

Examples:
  popctl scan
  popctl scan --source apt --manual-only
  popctl scan --export /tmp/scan.json
```

#### `popctl diff`

```
Show differences between manifest and current system state

Usage: popctl diff [options]

Shows:
  • Packages installed but not in manifest (new)
  • Packages in manifest but not installed (missing)
  • Configuration files that have drifted
  • Cleanup candidates

Options:
  --source SOURCE   Only diff specific source
  --brief           Show summary only
  --json            Output as JSON

Examples:
  popctl diff
  popctl diff --source apt --brief
```

#### `popctl apply`

```
Apply manifest to system

Usage: popctl apply [options]

This command:
  1. Installs packages listed in manifest but not on system
  2. Removes packages not in manifest (with confirmation)
  3. Syncs configuration files

Options:
  --dry-run         Show what would happen without changes
  --yes             Skip confirmation prompts (dangerous!)
  --install-only    Only install missing packages
  --remove-only     Only remove unwanted packages
  --config-only     Only sync configuration files

Examples:
  popctl apply --dry-run
  popctl apply --install-only
  popctl apply
```

#### `popctl clean`

```
Remove system cruft

Usage: popctl clean [options]

Cleans:
  • Orphaned packages (apt autoremove)
  • Unused Flatpak runtimes
  • Cache directories
  • Empty configuration directories
  • Old log files

Options:
  --dry-run         Show what would be cleaned
  --aggressive      Also clean large/old directories
  --cache-only      Only clean caches
  --orphans-only    Only remove orphaned packages

Examples:
  popctl clean --dry-run
  popctl clean --cache-only
  popctl clean --aggressive
```

#### `popctl config`

```
Manage configuration files

Usage: popctl config <subcommand> [options]

Subcommands:
  diff              Show differences between managed and system configs
  sync              Copy managed configs to system
  pull              Copy system configs to managed directory
  list              List all managed configurations

Options:
  --file PATH       Operate on specific file only

Examples:
  popctl config diff
  popctl config sync --dry-run
  popctl config pull ~/.config/nvim/init.lua
```

#### `popctl history`

```
Show change history

Usage: popctl history [options]

Options:
  --limit N         Show last N entries (default: 20)
  --since DATE      Show entries since date
  --json            Output as JSON

Examples:
  popctl history
  popctl history --limit 50
  popctl history --since 2026-01-01
```

#### `popctl undo`

```
Undo recent changes

Usage: popctl undo [options]

Options:
  --steps N         Undo last N operations (default: 1)
  --to TIMESTAMP    Undo to specific point in time
  --dry-run         Show what would be undone

Examples:
  popctl undo
  popctl undo --steps 3
  popctl undo --to "2026-01-24 15:30"
```

#### `popctl advisor`

```
Run Claude-assisted classification

Usage: popctl advisor <subcommand> [options]

Subcommands:
  init              Initial classification of all packages (takes 30-60 min)
  review            Review new/unknown packages
  analyze PATH      Analyze exported scan file

Options:
  --batch-size N    Packages per API call (default: 20)
  --interactive     Prompt for each uncertain package
  --auto            Use Claude's recommendations without prompting

Examples:
  popctl advisor init
  popctl advisor review --interactive
  popctl advisor analyze /tmp/scan.json
```

---

## 10. Security Model

### 10.1 Principles

1. **No unattended destructive actions**: Always require confirmation for removals
2. **Dry-run by default**: Commands show what would happen before doing it
3. **Trash before delete**: Configs are moved to trash, not deleted
4. **Reversible operations**: History enables undo
5. **Container isolation**: Claude Advisor runs in isolated container

### 10.2 Permission Model

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PERMISSION REQUIREMENTS                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Command              Permissions Required                                  │
│  ─────────────────────────────────────────────────────────────────────────  │
│  popctl init          User (creates ~/.config/popctl)                       │
│  popctl scan          User (reads package databases)                        │
│  popctl diff          User                                                  │
│  popctl apply         sudo (for apt operations)                             │
│  popctl clean         sudo (for apt/system cleanup)                         │
│  popctl config sync   User (writes to ~/)                                   │
│  popctl history       User                                                  │
│  popctl undo          sudo (may need to reinstall packages)                 │
│  popctl advisor       User (no system changes)                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 10.3 Protected Paths

These paths are NEVER modified by popctl:

```
~/.ssh/                     # SSH keys
~/.gnupg/                   # GPG keys
~/.password-store/          # Pass password store
~/.local/share/keyrings/    # GNOME Keyring
/etc/fstab                  # Mount configuration
/etc/passwd                 # User database
/etc/shadow                 # Password hashes
/boot/                      # Bootloader
```

### 10.4 API Key Security

```
Recommended: Use environment variable
$ export ANTHROPIC_API_KEY="sk-ant-..."

Alternative: Use keyring
$ secret-tool store --label="popctl" service popctl key anthropic_api_key

NOT Recommended: Plain text in config
# machine.toml
[api]
anthropic_api_key = "sk-..."  # Don't do this!
```

---

## 11. Tech Stack

### 11.1 Core Dependencies

| Component | Technology | Version | Purpose |
|-----------|------------|---------|---------|
| **Language** | Python | ≥3.11 | Core implementation |
| **Package Manager** | uv | Latest | Dependency management |
| **CLI Framework** | Typer | ≥0.12 | Command-line interface |
| **Rich Output** | Rich | ≥13.0 | Terminal formatting |
| **Config Parsing** | tomli/tomli-w | ≥2.0 | TOML read/write |
| **Data Validation** | Pydantic | ≥2.0 | Schema validation |
| **AI Integration** | anthropic | ≥0.40 | Claude API client |

### 11.2 System Dependencies

| Tool | Purpose | Installation |
|------|---------|--------------|
| `apt` | APT package operations | System (pre-installed) |
| `dpkg` | Package database queries | System (pre-installed) |
| `flatpak` | Flatpak operations | System (pre-installed on Pop!_OS) |
| `snap` | Snap operations (optional) | `apt install snapd` |
| `dconf` | GNOME/COSMIC settings | System (pre-installed) |

### 11.3 Python Project Structure

```
popctl/
├── pyproject.toml
├── README.md
├── LICENSE
├── src/
│   └── popctl/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli/
│       │   ├── __init__.py
│       │   ├── main.py
│       │   └── commands/
│       │       ├── init.py
│       │       ├── scan.py
│       │       ├── diff.py
│       │       ├── apply.py
│       │       ├── clean.py
│       │       ├── config.py
│       │       ├── history.py
│       │       └── advisor.py
│       ├── core/
│       │   ├── manifest.py
│       │   ├── machine.py
│       │   ├── state.py
│       │   └── config.py
│       ├── scanners/
│       │   ├── base.py
│       │   ├── apt.py
│       │   ├── flatpak.py
│       │   ├── snap.py
│       │   ├── filesystem.py
│       │   └── dconf.py
│       ├── operators/
│       │   ├── base.py
│       │   ├── apt.py
│       │   ├── flatpak.py
│       │   ├── snap.py
│       │   ├── filesystem.py
│       │   └── configs.py
│       ├── advisor/
│       │   ├── client.py
│       │   ├── classifier.py
│       │   ├── prompts.py
│       │   └── cache.py
│       ├── models/
│       │   ├── package.py
│       │   ├── scan_result.py
│       │   └── change.py
│       └── utils/
│           ├── shell.py
│           ├── paths.py
│           └── toml.py
└── tests/
    ├── conftest.py
    ├── test_manifest.py
    ├── test_scanners/
    ├── test_operators/
    └── fixtures/
```

### 11.4 pyproject.toml

```toml
[project]
name = "popctl"
version = "0.1.0"
description = "Declarative system configuration for Pop!_OS"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.11"
authors = [
    { name = "Your Name", email = "you@example.com" }
]
keywords = ["linux", "pop-os", "system-management", "dotfiles", "declarative"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Operating System :: POSIX :: Linux",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: System :: Systems Administration",
]

dependencies = [
    "typer>=0.12.0",
    "rich>=13.0.0",
    "tomli>=2.0.0;python_version<'3.11'",
    "tomli-w>=1.0.0",
    "anthropic>=0.40.0",
    "pydantic>=2.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=4.0.0",
    "pytest-mock>=3.12.0",
    "ruff>=0.5.0",
    "mypy>=1.10.0",
]

[project.scripts]
popctl = "popctl.cli.main:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/popctl"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "N"]

[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_ignores = true

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra -q --cov=popctl"
```

---

## 12. Implementation Roadmap

### 12.1 Phase Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         IMPLEMENTATION ROADMAP                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Phase 1: Foundation (MVP)                              ~2 weeks            │
│  ════════════════════════════════════════════════════════════════           │
│  ✓ Project scaffolding with uv                                              │
│  ✓ Manifest format and parsing                                              │
│  ✓ APT scanner (full implementation)                                        │
│  ✓ Flatpak scanner                                                          │
│  ✓ Basic CLI: init, scan, diff                                              │
│  ✓ Pop!_OS 24.04 baseline definition                                        │
│                                                                             │
│  Deliverable: Can scan system and show differences from baseline            │
│                                                                             │
│  Phase 2: Core Operations                               ~2 weeks            │
│  ════════════════════════════════════════════════════════════════           │
│  ✓ APT operator (install/remove)                                            │
│  ✓ Flatpak operator                                                         │
│  ✓ Snap scanner and operator                                                │
│  ✓ CLI: apply command with dry-run                                          │
│  ✓ State management (history, undo)                                         │
│                                                                             │
│  Deliverable: Can apply manifest to system with rollback                    │
│                                                                             │
│  Phase 3: Claude Advisor                                ~1 week             │
│  ════════════════════════════════════════════════════════════════           │
│  ✓ Classification prompt engineering                                        │
│  ✓ Anthropic API integration                                                │
│  ✓ Response caching                                                         │
│  ✓ Interactive TUI for decisions                                            │
│  ✓ Container integration (exchange directory)                               │
│                                                                             │
│  Deliverable: AI-assisted package classification working                    │
│                                                                             │
│  Phase 4: Config Management                             ~1 week             │
│  ════════════════════════════════════════════════════════════════           │
│  ✓ Config sync/diff/pull commands                                           │
│  ✓ Trash system for deleted configs                                         │
│  ✓ Git integration for versioning                                           │
│                                                                             │
│  Deliverable: Full dotfile management                                       │
│                                                                             │
│  Phase 5: Filesystem Cleanup                            ~1 week             │
│  ════════════════════════════════════════════════════════════════           │
│  ✓ Filesystem scanner                                                       │
│  ✓ Cruft pattern matching                                                   │
│  ✓ Orphan config detection                                                  │
│  ✓ Clean command implementation                                             │
│                                                                             │
│  Deliverable: Automated cruft cleanup                                       │
│                                                                             │
│  Phase 6: Polish & Documentation                        ~1 week             │
│  ════════════════════════════════════════════════════════════════           │
│  ✓ Comprehensive test suite                                                 │
│  ✓ Error handling improvements                                              │
│  ✓ Documentation (README, man pages)                                        │
│  ✓ Packaging for distribution                                               │
│                                                                             │
│  Deliverable: Production-ready release                                      │
│                                                                             │
│  TOTAL ESTIMATED TIME: 8 weeks                                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 12.2 Phase 1 Milestones

| Week | Milestone | Acceptance Criteria |
|------|-----------|---------------------|
| 1.1 | Project Setup | uv project, CI pipeline, basic CLI structure |
| 1.2 | Manifest Parser | Load/save TOML, validate schema, handle includes |
| 1.3 | APT Scanner | List all packages, distinguish manual/auto |
| 1.4 | Flatpak Scanner | List apps and runtimes |
| 1.5 | Diff Command | Compare manifest vs system, show changes |
| 1.6 | Baseline | Pop!_OS 24.04 default package list |

### 12.3 Future Enhancements (Post-MVP)

| Feature | Priority | Complexity |
|---------|----------|------------|
| COSMIC dconf settings | P2 | Medium |
| Systemd timer for periodic checks | P2 | Low |
| Desktop notifications | P2 | Low |
| Web UI dashboard | P3 | High |
| Multi-distro support | P3 | High |
| Declarative apt sources management | P2 | Medium |
| Kernel version pinning | P2 | Medium |

---

## 13. Testing Strategy

### 13.1 Test Categories

| Category | Purpose | Tools |
|----------|---------|-------|
| **Unit Tests** | Test individual functions | pytest |
| **Integration Tests** | Test component interaction | pytest + fixtures |
| **System Tests** | Test against real system (VM) | pytest + Vagrant/Docker |
| **Snapshot Tests** | Verify output stability | pytest-snapshot |

### 13.2 Test Structure

```
tests/
├── conftest.py                 # Shared fixtures
├── unit/
│   ├── test_manifest.py        # Manifest parsing
│   ├── test_scanners/
│   │   ├── test_apt.py
│   │   ├── test_flatpak.py
│   │   └── test_filesystem.py
│   └── test_operators/
│       └── test_apt.py
├── integration/
│   ├── test_cli_commands.py
│   ├── test_advisor.py
│   └── test_state.py
├── system/
│   └── test_full_workflow.py   # End-to-end in VM
└── fixtures/
    ├── manifests/
    │   └── sample-manifest.toml
    └── scan_results/
        └── sample-scan.json
```

### 13.3 Mock Strategies

```python
# conftest.py

import pytest
from unittest.mock import MagicMock

@pytest.fixture
def mock_apt_output():
    """Mock dpkg-query output."""
    return """firefox\t128.0\t204800\tMozilla Firefox web browser
neovim\t0.9.5\t51200\tVim-based text editor
libgtk-3-0\t3.24.41\t10240\tGTK graphical toolkit"""

@pytest.fixture
def mock_subprocess(monkeypatch, mock_apt_output):
    """Mock subprocess.run for scanner tests."""
    def mock_run(cmd, *args, **kwargs):
        result = MagicMock()
        if "dpkg-query" in cmd:
            result.stdout = mock_apt_output
        elif "apt-mark" in cmd:
            result.stdout = "libgtk-3-0"
        result.returncode = 0
        return result
    
    monkeypatch.setattr("subprocess.run", mock_run)

@pytest.fixture
def sample_manifest(tmp_path):
    """Create sample manifest for testing."""
    manifest_dir = tmp_path / ".config" / "popctl"
    manifest_dir.mkdir(parents=True)
    
    manifest = manifest_dir / "manifest.toml"
    manifest.write_text("""
[system]
name = "test-system"
base = "pop-os-24.04"

[packages.apt.keep]
cli = ["neovim", "ripgrep"]
""")
    
    return manifest_dir
```

### 13.4 CI Pipeline

```yaml
# .github/workflows/ci.yml
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      
      - name: Install uv
        uses: astral-sh/setup-uv@v4
      
      - name: Install dependencies
        run: uv sync --dev
      
      - name: Run linter
        run: uv run ruff check src/ tests/
      
      - name: Run type checker
        run: uv run mypy src/
      
      - name: Run tests
        run: uv run pytest --cov=popctl --cov-report=xml
      
      - name: Upload coverage
        uses: codecov/codecov-action@v4
```

---

## 14. Appendices

### Appendix A: Glossary

| Term | Definition |
|------|------------|
| **Baseline** | Default package set for a fresh OS installation |
| **Cruft** | Unnecessary files, caches, or orphaned configs |
| **Manifest** | TOML file defining desired system state |
| **Orphan** | Package installed as dependency but no longer needed |
| **Scanner** | Component that reads current system state |
| **Operator** | Component that modifies system state |

### Appendix B: Related Tools

| Tool | Description | Relationship to popctl |
|------|-------------|------------------------|
| **Chezmoi** | Dotfile manager | popctl handles dotfiles differently (copy vs symlink) |
| **Ansible** | Configuration management | popctl is simpler, single-user focused |
| **NixOS** | Declarative Linux distro | Similar philosophy, but popctl works with existing distro |
| **Timeshift** | System snapshots | Complementary; popctl makes Timeshift less necessary |
| **deborphan** | Find orphaned packages | popctl incorporates this functionality |

### Appendix C: Common Package Categories

For Claude Advisor classification:

| Category | Examples | Default Action |
|----------|----------|----------------|
| **system** | systemd, linux-*, libc | Keep |
| **desktop** | cosmic-*, gnome-* | Keep |
| **drivers** | nvidia-*, intel-*, firmware-* | Keep |
| **development** | gcc, make, git | Ask user |
| **server** | apache, nginx, mysql | Ask user |
| **media** | vlc, spotify, gimp | Ask user |
| **gaming** | steam, lutris | Ask user |
| **office** | libreoffice, thunderbird | Ask user |
| **network** | networkmanager, curl | Keep |
| **security** | gnupg, openssh | Keep |
| **obsolete** | python2, deprecated-* | Remove |
| **telemetry** | apport, whoopsie | Remove |

### Appendix D: Quick Start Guide

```bash
# 1. Install popctl
pipx install popctl
# or: uvx popctl

# 2. Initialize (scan system, create manifest)
popctl init

# 3. Export scan for Claude Advisor (in container)
popctl scan --export /tmp/popctl-exchange/scan.json

# 4. Run Claude Advisor (in ai-dev container)
# This will interactively classify all packages
popctl advisor init /home/dev/popctl-exchange/scan.json

# 5. Review diff between manifest and system
popctl diff

# 6. Apply manifest (dry-run first!)
popctl apply --dry-run
popctl apply

# 7. Clean up cruft
popctl clean --dry-run
popctl clean

# 8. Regular maintenance (weekly)
popctl diff
popctl apply
popctl clean
```

### Appendix E: Troubleshooting

| Problem | Solution |
|---------|----------|
| `apt` operations fail | Run with `sudo` or check permissions |
| Claude API errors | Check `ANTHROPIC_API_KEY` environment variable |
| Manifest validation fails | Run `popctl validate` for detailed errors |
| Undo doesn't restore package | Package was purged (config deleted); reinstall manually |
| Config sync conflicts | Use `popctl config diff` to review, then choose direction |

---

## Document History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0-draft | 2026-01-24 | Initial specification |

---

*This specification is a living document. Updates will be tracked in version control.*
