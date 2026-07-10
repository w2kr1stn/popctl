"""Development task entry points for popctl (invoked via uv run)."""

# Run: uv run python devops.py <fmt|test|clean>; direct Ruff/Pytest commands work too.

import subprocess
import sys


def _run(commands: list[list[str]]) -> None:
    """Execute a sequence of shell commands, exiting on first failure."""
    for cmd in commands:
        try:
            subprocess.run(cmd, check=True)  # nosec: B603, B607
        except subprocess.CalledProcessError as e:
            print(f"Command failed: {' '.join(e.cmd)}", file=sys.stderr)
            sys.exit(e.returncode)


def format_code() -> None:
    """Format the codebase with Ruff."""
    _run(
        [
            ["echo", "🎨 [Native Task] Formatting with Ruff...\n"],
            ["ruff", "format", "."],
            ["ruff", "check", "--fix", "."],
            ["echo", "\n🟢 Made everything pretty → ✅ Code clean."],
        ]
    )


def test() -> None:
    """Run tests with PyTest."""
    _run(
        [
            ["echo", "🧪 [Native Task] Testing with PyTest...\n"],
            ["uv", "run", "pytest", "-q"],
            ["echo", "\n🟢 Test Coverage → ✅ Test coverage sufficient"],
        ]
    )


def clean() -> None:
    """Clean up the project."""
    _run(
        [
            ["echo", "🧹 [Native Task] Cleaning the Project...\n"],
            # ---------------------
            # Basic clean up
            # ----------------------
            ["find", ".", "-type", "d", "-name", "__pycache__", "-exec", "rm", "-rf", "{}", "+"],
            ["find", ".", "-type", "f", "-name", "*.pyc", "-delete"],
            # ----------------------
            # Extended clean up
            # ----------------------
            ## Python cache files
            # ["find", ".", "-type", "d", "-name", "__pycache__", "-exec", "rm", "-rf", "{}", "+"],
            # ["find", ".", "-type", "f", "-name", "*.pyc", "-delete"],
            # ["find", ".", "-type", "f", "-name", "*.pyo", "-delete"],
            # ["find", ".", "-type", "f", "-name", "*.pyd", "-delete"],
            # ## Test and coverage artifacts
            # ["rm", "-rf", ".pytest_cache"],
            # ["rm", "-rf", ".coverage"],
            # ["rm", "-rf", "htmlcov"],
            # ## Tool caches
            # ["rm", "-rf", ".ruff_cache"],
            # ["rm", "-rf", ".mypy_cache"],
            # ## Build artifacts
            # ["rm", "-rf", "dist"],
            # ["rm", "-rf", "build"],
            # ["find", ".", "-type", "d", "-name", "*.egg-info", "-exec", "rm", "-rf", "{}", "+"],
            ["echo", "\n🟢 Caches & Artifacts → ✅ All fresh now"],
        ]
    )


def main() -> None:
    """Run a named development task."""
    tasks = {"fmt": format_code, "test": test, "clean": clean}
    if len(sys.argv) != 2 or sys.argv[1] not in tasks:
        print("Usage: uv run python devops.py <fmt|test|clean>", file=sys.stderr)
        sys.exit(2)
    tasks[sys.argv[1]]()


if __name__ == "__main__":
    main()
