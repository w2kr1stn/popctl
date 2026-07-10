# Contributing

## Development

Set up the development environment from a checkout:

```bash
uv sync
```

Run the quality gates before opening a pull request:

```bash
uv run ruff check .
uv run pyright app/
uv run pytest
```

Tests enforce an aggregate coverage floor of 81%.

## Changes

Use Conventional Commit messages written in English. Open pull requests against
the `master` branch and include tests or documentation for user-visible changes.
