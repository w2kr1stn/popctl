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
# Show all APT packages
popctl scan

# Show only manually installed packages
popctl scan --manual-only

# Show package counts only
popctl scan --count

# Limit output to first N packages
popctl scan --limit 20
```

### Command Line Options

```
popctl --help           # Show main help
popctl --version        # Show version
popctl scan --help      # Show scan command help
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
│       └── scan.py      # Scan command implementation
├── models/
│   └── package.py       # PackageSource, PackageStatus, ScannedPackage
├── scanners/
│   ├── base.py          # Scanner ABC
│   └── apt.py           # AptScanner implementation
└── utils/
    ├── shell.py         # Subprocess helpers
    └── formatting.py    # Rich console formatting
```

## Roadmap

- [x] PoC-1: Hello APT - Scan APT packages
- [ ] PoC-2: Multi-Source - Flatpak scanner + export
- [ ] PoC-3: Manifest Birth - Generate manifest from scan
- [ ] PoC-4: Diff Engine - Compare manifest vs system
- [ ] MVP-1: First Apply - Install/remove packages
- [ ] MVP-2: Claude Advisor - AI-assisted classification
- [ ] MVP-3: Safety Net - History and undo

## License

MIT
