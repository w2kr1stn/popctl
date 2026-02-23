# CLAUDE.md — Development Workflow & Conventions

## Agent Instructions
- **Communication**: Chat in **German**, Code & Docs in **English**.
- **QA Gate**: Tests >= 85% (current: 92%), Pyright: 0 errors in `app/`. Fail on Bandit Medium/High.
- **PRs**: Base branch = Story-Branch. NEVER merge to `main`.

## Operational Commands
- **Quality gates**: `uv run fmt && uv run pyright app/ && uv run test`
- **Pyright scope**: Run on `app/` (not `.`) — test files produce non-blocking warnings
- **No external services**: Pure local CLI tool, no database, no network dependencies

## Git Commits
- **Exclude from commits**: `TASKS.md`, `PROJECT.md` / `FEATURE.md`, `SESSION*.md`, this `CLAUDE.md` file
- **Avoid**: AI-generated footers in commit messages
- **Never** use `git commit -i` or `git rebase -i` (interactive mode not supported)

## Code Patterns
- **Immutable dataclasses**: Use `@dataclass(frozen=True, slots=True)` for all models
- **Pydantic**: Use for config validation and TOML/JSON serialization (manifest, advisor config)
- **Subprocess**: Use `utils/shell.py` helpers, never raw subprocess
- **File I/O**: Use `core/paths.py` for XDG-compliant paths (`get_config_dir()`, `get_state_dir()`, `ensure_dir()`)
- **History**: JSONL format with `HistoryEntry.to_json_line()`/`from_json_line()`
- **Manifest loading**: Use `require_manifest()` from `cli/types.py` (handles errors + typer.Exit)
- **Scanner setup**: Use `get_scanners()` / `get_available_scanners()` from `scanners/__init__.py`; `get_checked_scanners()` from `cli/types.py`
- **Shared CLI enums**: `SourceChoice` lives in `cli/types.py`, not in individual commands
- **Immutable collections**: Use `tuple` instead of `list` in frozen dataclasses
- **Literal types**: Use `Literal[...]` for closed string domains (not bare `str`)
- **Cross-field validation**: Use Pydantic `@model_validator(mode="after")` for invariants
- **Protected packages**: Check `is_package_protected()` from `core/baseline.py` in ALL package paths
- **Protected paths**: Check `is_protected(path, domain)` from `domain/protected.py` in ALL destructive domain paths (CLI + operator level, defense-in-depth)
- **Decision application**: Use `apply_decisions_to_manifest()` from `advisor/exchange.py` for package decisions; `apply_domain_decisions_to_manifest()` for filesystem/config decisions
- **System scanning**: Use `scan_system()` from `advisor/scanning.py` (framework-agnostic, raises RuntimeError). CLI callers wrap with try/except RuntimeError -> typer.Exit
- **Path classification**: Use `classify_path_type()` from `domain/ownership.py` (shared by FilesystemScanner and ConfigScanner)
- **Domain orphan collection**: Use `collect_domain_orphans()` from `cli/types.py`
- **Diff computation**: Use `compute_system_diff()` from `cli/types.py` (combines manifest load + scan + diff)
- **Enum mapping**: Use explicit dict mappings (e.g., `_PACKAGE_TO_HISTORY`, `INVERSE_ACTION_TYPES`) instead of runtime string conversion between enum types
- **Executor dispatch**: `execute_actions()` in `core/executor.py` groups actions by source, then splits into install/remove/purge batches per operator

## Error Handling
- **Never** use bare `except Exception` — always catch specific types (`OSError`, `RuntimeError`)
- **Always** show user-visible warnings (via `print_warning`) when non-critical operations fail
- **Avoid** `contextlib.suppress()` for file operations — use explicit try/except with logging
- **Corrupt data**: Count and log summary (e.g., corrupt history lines), don't silently skip
- **Advisor failures**: Always non-fatal — print warning and continue with existing manifest
- **Domain operations**: Per-path error isolation — one failure does not abort remaining paths

## Testing Patterns
- **CLI Tests**: Use `typer.testing.CliRunner` with `app` from `cli/main.py`
- **File I/O Tests**: Use `tmp_path` fixture, mock `get_*_dir()` paths
- **Subprocess Tests**: Mock `subprocess.run`, never call real package managers
- **Coverage Target**: 85%+ per module, verify with `uv run pytest --cov`
- **Shared fixtures**: `sample_manifest` in `tests/unit/conftest.py` (includes APT + Flatpak packages)
- **Domain parametrization**: Use `@pytest.mark.parametrize("domain", ["filesystem", "configs"])` for dual-domain tests
- **Generic domain access**: Use `getattr(manifest, domain)` and `**{domain: config}` kwargs pattern in tests

## Agent Workflow (backend-engineer)
- Always read relevant docs first: `IMPLEMENTATION.md`, `PROJECT.md`, `TASKS.yml`
- Include file paths in prompts for context
- Run quality gates after each task: `uv run fmt && uv run pyright app/ && uv run test`
- Commit with `Refs: task-id` at end of message body

## Review Workflow
- **PR Review**: Launch parallel agents (code-reviewer, silent-failure-hunter, type-design-analyzer, pr-test-analyzer), then aggregate results
- **Security Review**: Use code-reviewer with security-focused prompt, validate findings with false-positive filter
- **Code Simplification**: Use general-purpose agent to find overengineering and DRY violations (multi-agent 4-pass review)
- **Final QA**: Use qa-specialist for overall quality gate check (tests, coverage, architecture compliance, Bandit)

## Overengineering Review Process
When reviewing for overengineering:
1. Launch 4 parallel agents scanning separate parts of the codebase
2. Collect findings, deduplicate, assign priority (A=high → D=low)
3. Create wave-based plan (5-7 waves, sequential, verify after each)
4. Defer items with clear justification (language constraints, UX requirements, etc.)
5. Verify after each wave: `uv run fmt && uv run pyright app/ && uv run test`

## Common Gotchas
- `PROJECT.md`, `TASKS.yml`, `SESSION*.md` are NOT tracked in git
- AI agents may auto-commit — verify no AI footer before pushing
- Bandit B108 on `/tmp/` paths is accepted (documented exchange protocol)
- Pyright strict mode on test files produces warnings — source code must be clean, tests are non-blocking
- `_INVERSE_ACTION_TYPES` was renamed to `INVERSE_ACTION_TYPES` (public) since it's used by `undo.py`
- Advisor workspace dir is per-session at `~/.local/state/popctl/advisor-sessions/<timestamp>/`
- `popctl advisor {classify,session}` only handles packages; domain orphan advising is sync-only
