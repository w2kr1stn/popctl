"""Unit tests for prompt templates and builders.

Tests for the advisor prompts module that provides prompt templates for
AI-assisted package classification in headless and workspace-based session modes.
"""

import re
from pathlib import Path

from popctl.advisor.prompts import (
    CATEGORIES,
    DECISIONS_SCHEMA,
    HEADLESS_PROMPT,
    INITIAL_PROMPT,
    SESSION_CLAUDE_MD,
    build_headless_prompt,
    build_initial_prompt,
    build_session_claude_md,
    get_decisions_schema,
    get_prompt_file_path,
)


class TestCategories:
    """Tests for the CATEGORIES constant."""

    def test_categories_is_tuple(self) -> None:
        """CATEGORIES is an immutable tuple."""
        assert isinstance(CATEGORIES, tuple)

    def test_categories_has_expected_values(self) -> None:
        """CATEGORIES contains all expected classification categories."""
        expected = {
            "system",
            "desktop",
            "drivers",
            "development",
            "server",
            "media",
            "gaming",
            "office",
            "network",
            "security",
            "obsolete",
            "telemetry",
            "other",
        }
        assert set(CATEGORIES) == expected

    def test_categories_not_empty(self) -> None:
        """CATEGORIES is not empty."""
        assert len(CATEGORIES) > 0


class TestHeadlessPromptTemplate:
    """Tests for the HEADLESS_PROMPT template string."""

    def test_headless_prompt_has_classification_rules(self) -> None:
        """HEADLESS_PROMPT contains classification rules for keep/remove/ask."""
        assert "KEEP" in HEADLESS_PROMPT
        assert "REMOVE" in HEADLESS_PROMPT
        assert "ASK" in HEADLESS_PROMPT

    def test_headless_prompt_has_confidence_thresholds(self) -> None:
        """HEADLESS_PROMPT mentions confidence thresholds."""
        assert "0.9" in HEADLESS_PROMPT or "confidence" in HEADLESS_PROMPT.lower()

    def test_headless_prompt_has_placeholders(self) -> None:
        """HEADLESS_PROMPT has required format placeholders."""
        assert "{scan_json_path}" in HEADLESS_PROMPT
        assert "{decisions_output_path}" in HEADLESS_PROMPT
        assert "{system_context}" in HEADLESS_PROMPT
        assert "{categories}" in HEADLESS_PROMPT
        assert "{timestamp}" in HEADLESS_PROMPT

    def test_headless_prompt_mentions_toml_format(self) -> None:
        """HEADLESS_PROMPT describes TOML output format."""
        assert "TOML" in HEADLESS_PROMPT or "toml" in HEADLESS_PROMPT.lower()

    def test_headless_prompt_mentions_decisions_schema(self) -> None:
        """HEADLESS_PROMPT shows decisions.toml structure."""
        assert "[packages.apt]" in HEADLESS_PROMPT
        assert "keep" in HEADLESS_PROMPT
        assert "remove" in HEADLESS_PROMPT
        assert "ask" in HEADLESS_PROMPT

    def test_headless_prompt_mentions_pop_os(self) -> None:
        """HEADLESS_PROMPT mentions Pop!_OS context."""
        assert "Pop!_OS" in HEADLESS_PROMPT or "pop-os" in HEADLESS_PROMPT.lower()


class TestSessionClaudeMdTemplate:
    """Tests for the SESSION_CLAUDE_MD template string."""

    def test_session_claude_md_is_markdown(self) -> None:
        """SESSION_CLAUDE_MD is valid Markdown with headers."""
        assert "# " in SESSION_CLAUDE_MD
        assert "## " in SESSION_CLAUDE_MD

    def test_session_claude_md_has_placeholders(self) -> None:
        """SESSION_CLAUDE_MD has required format placeholders."""
        assert "{system_context}" in SESSION_CLAUDE_MD
        assert "{categories}" in SESSION_CLAUDE_MD

    def test_session_claude_md_has_classification_rules(self) -> None:
        """SESSION_CLAUDE_MD contains classification guidelines."""
        assert "KEEP" in SESSION_CLAUDE_MD
        assert "REMOVE" in SESSION_CLAUDE_MD
        assert "Discuss uncertain packages" in SESSION_CLAUDE_MD

    def test_session_claude_md_mentions_files(self) -> None:
        """SESSION_CLAUDE_MD mentions input/output files."""
        assert "scan.json" in SESSION_CLAUDE_MD
        assert "manifest" in SESSION_CLAUDE_MD.lower()
        assert "decisions.toml" in SESSION_CLAUDE_MD

    def test_session_claude_md_mentions_output_dir(self) -> None:
        """SESSION_CLAUDE_MD specifies output directory."""
        assert "output/decisions.toml" in SESSION_CLAUDE_MD

    def test_session_claude_md_has_protected_packages(self) -> None:
        """SESSION_CLAUDE_MD lists protected package patterns."""
        assert "linux-*" in SESSION_CLAUDE_MD
        assert "systemd" in SESSION_CLAUDE_MD

    def test_session_claude_md_emphasizes_lean_host(self) -> None:
        """SESSION_CLAUDE_MD emphasizes lean host philosophy."""
        assert "lean" in SESSION_CLAUDE_MD.lower()
        assert "removal" in SESSION_CLAUDE_MD.lower()


class TestInitialPrompt:
    """Tests for the INITIAL_PROMPT constant."""

    def test_initial_prompt_is_string(self) -> None:
        """INITIAL_PROMPT is a non-empty string."""
        assert isinstance(INITIAL_PROMPT, str)
        assert len(INITIAL_PROMPT) > 0

    def test_initial_prompt_mentions_scan_json(self) -> None:
        """INITIAL_PROMPT references scan.json."""
        assert "scan.json" in INITIAL_PROMPT

    def test_initial_prompt_mentions_decisions(self) -> None:
        """INITIAL_PROMPT references decisions output."""
        assert "decisions.toml" in INITIAL_PROMPT

    def test_initial_prompt_mentions_claude_md(self) -> None:
        """INITIAL_PROMPT references CLAUDE.md."""
        assert "CLAUDE.md" in INITIAL_PROMPT


class TestDecisionsSchema:
    """Tests for the DECISIONS_SCHEMA template."""

    def test_decisions_schema_has_placeholders(self) -> None:
        """DECISIONS_SCHEMA has format placeholders."""
        assert "{date}" in DECISIONS_SCHEMA
        assert "{provider}" in DECISIONS_SCHEMA

    def test_decisions_schema_has_package_sections(self) -> None:
        """DECISIONS_SCHEMA has apt and flatpak sections."""
        assert "[packages.apt]" in DECISIONS_SCHEMA
        assert "[packages.flatpak]" in DECISIONS_SCHEMA

    def test_decisions_schema_has_classification_arrays(self) -> None:
        """DECISIONS_SCHEMA has keep/remove/ask arrays."""
        assert "keep = [" in DECISIONS_SCHEMA
        assert "remove = [" in DECISIONS_SCHEMA
        assert "ask = [" in DECISIONS_SCHEMA


class TestBuildHeadlessPrompt:
    """Tests for build_headless_prompt function."""

    def test_build_headless_prompt_includes_paths(self) -> None:
        """build_headless_prompt includes scan and decisions paths."""
        prompt = build_headless_prompt(
            "/tmp/popctl-exchange/scan.json",
            "/tmp/popctl-exchange/decisions.toml",
        )

        assert "/tmp/popctl-exchange/scan.json" in prompt
        assert "/tmp/popctl-exchange/decisions.toml" in prompt

    def test_build_headless_prompt_includes_categories(self) -> None:
        """build_headless_prompt includes all categories."""
        prompt = build_headless_prompt(
            "/tmp/scan.json",
            "/tmp/decisions.toml",
        )

        for category in CATEGORIES:
            assert category in prompt

    def test_build_headless_prompt_includes_timestamp(self) -> None:
        """build_headless_prompt includes a timestamp."""
        prompt = build_headless_prompt(
            "/tmp/scan.json",
            "/tmp/decisions.toml",
        )

        # ISO 8601 format: YYYY-MM-DDTHH:MM:SSZ
        timestamp_pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
        assert re.search(timestamp_pattern, prompt) is not None

    def test_build_headless_prompt_with_system_info(self) -> None:
        """build_headless_prompt includes system info when provided."""
        system_info = {
            "hostname": "pop-desktop",
            "os": "Pop!_OS 24.04",
            "machine_id": "desktop-home-abc123",
        }

        prompt = build_headless_prompt(
            "/tmp/scan.json",
            "/tmp/decisions.toml",
            system_info=system_info,
        )

        assert "pop-desktop" in prompt
        assert "Pop!_OS 24.04" in prompt
        assert "desktop-home-abc123" in prompt

    def test_build_headless_prompt_without_system_info(self) -> None:
        """build_headless_prompt works without system info."""
        prompt = build_headless_prompt(
            "/tmp/scan.json",
            "/tmp/decisions.toml",
            system_info=None,
        )

        # Should still be valid prompt
        assert "scan.json" in prompt
        assert "decisions.toml" in prompt
        # No extra context lines
        assert "**Hostname**" not in prompt

    def test_build_headless_prompt_partial_system_info(self) -> None:
        """build_headless_prompt handles partial system info."""
        system_info = {"hostname": "my-host"}

        prompt = build_headless_prompt(
            "/tmp/scan.json",
            "/tmp/decisions.toml",
            system_info=system_info,
        )

        assert "my-host" in prompt
        # Missing keys should not cause errors
        assert "**OS Version**" not in prompt

    def test_build_headless_prompt_has_classification_rules(self) -> None:
        """build_headless_prompt includes classification rules."""
        prompt = build_headless_prompt(
            "/tmp/scan.json",
            "/tmp/decisions.toml",
        )

        # Check for key rules
        assert "System-critical" in prompt or "system-critical" in prompt.lower()
        assert "telemetry" in prompt.lower()


class TestBuildSessionClaudeMd:
    """Tests for build_session_claude_md function."""

    def test_build_session_claude_md_returns_markdown(self) -> None:
        """build_session_claude_md returns valid Markdown."""
        result = build_session_claude_md()

        assert result.startswith("#")
        assert "## " in result

    def test_build_session_claude_md_includes_categories(self) -> None:
        """build_session_claude_md includes all categories."""
        result = build_session_claude_md()

        for category in CATEGORIES:
            assert f"`{category}`" in result

    def test_build_session_claude_md_with_system_info(self) -> None:
        """build_session_claude_md includes system context."""
        system_info = {"hostname": "pop-desktop", "os": "Pop!_OS 24.04"}

        result = build_session_claude_md(system_info=system_info)

        assert "pop-desktop" in result
        assert "Pop!_OS 24.04" in result

    def test_build_session_claude_md_with_summary(self) -> None:
        """build_session_claude_md includes package summary."""
        summary = {"total": 500, "manual": 150}

        result = build_session_claude_md(summary=summary)

        assert "500" in result
        assert "150" in result

    def test_build_session_claude_md_without_args(self) -> None:
        """build_session_claude_md works without arguments."""
        result = build_session_claude_md()

        assert "scan.json" in result
        assert "decisions.toml" in result

    def test_build_session_claude_md_includes_timestamp(self) -> None:
        """build_session_claude_md includes a timestamp."""
        result = build_session_claude_md()

        timestamp_pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
        assert re.search(timestamp_pattern, result) is not None

    def test_build_session_claude_md_has_output_format(self) -> None:
        """build_session_claude_md includes output format specification."""
        result = build_session_claude_md()

        assert "output/decisions.toml" in result
        assert "[packages.apt]" in result

    def test_build_session_claude_md_has_protected_packages(self) -> None:
        """build_session_claude_md lists protected package patterns."""
        result = build_session_claude_md()

        assert "linux-*" in result
        assert "systemd" in result


class TestBuildInitialPrompt:
    """Tests for build_initial_prompt function."""

    def test_build_initial_prompt_returns_string(self) -> None:
        """build_initial_prompt returns a non-empty string."""
        result = build_initial_prompt()

        assert isinstance(result, str)
        assert len(result) > 0

    def test_build_initial_prompt_returns_constant(self) -> None:
        """build_initial_prompt returns the INITIAL_PROMPT constant."""
        assert build_initial_prompt() == INITIAL_PROMPT

    def test_build_initial_prompt_mentions_key_files(self) -> None:
        """build_initial_prompt references key files."""
        result = build_initial_prompt()

        assert "scan.json" in result
        assert "CLAUDE.md" in result
        assert "decisions.toml" in result


class TestGetDecisionsSchema:
    """Tests for get_decisions_schema function."""

    def test_get_decisions_schema_default_provider(self) -> None:
        """get_decisions_schema uses claude as default provider."""
        schema = get_decisions_schema()

        assert "claude" in schema.lower()

    def test_get_decisions_schema_custom_provider(self) -> None:
        """get_decisions_schema accepts custom provider."""
        schema = get_decisions_schema(provider="gemini")

        assert "gemini" in schema.lower()

    def test_get_decisions_schema_has_timestamp(self) -> None:
        """get_decisions_schema includes timestamp."""
        schema = get_decisions_schema()

        timestamp_pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
        assert re.search(timestamp_pattern, schema) is not None

    def test_get_decisions_schema_is_valid_toml_structure(self) -> None:
        """get_decisions_schema shows valid TOML structure."""
        schema = get_decisions_schema()

        assert "[packages.apt]" in schema
        assert "[packages.flatpak]" in schema
        assert "keep = [" in schema
        assert "remove = [" in schema
        assert "ask = [" in schema


class TestPathHelpers:
    """Tests for path helper functions."""

    def test_get_prompt_file_path(self, tmp_path: Path) -> None:
        """get_prompt_file_path returns correct path."""
        result = get_prompt_file_path(tmp_path)

        assert result == tmp_path / "prompt.txt"
        assert result.name == "prompt.txt"

    def test_path_helpers_with_nested_path(self, tmp_path: Path) -> None:
        """Path helpers work with nested directories."""
        exchange_dir = tmp_path / "nested" / "exchange"

        prompt_path = get_prompt_file_path(exchange_dir)

        assert prompt_path.parent == exchange_dir


class TestPromptContentQuality:
    """Tests for prompt content quality and completeness."""

    def test_headless_prompt_mentions_protected_packages(self) -> None:
        """Headless prompt mentions packages that should never be removed."""
        prompt = build_headless_prompt(
            "/tmp/scan.json",
            "/tmp/decisions.toml",
        )

        # Should mention system-critical patterns
        protected_keywords = ["kernel", "systemd", "driver"]
        matches = sum(1 for kw in protected_keywords if kw in prompt.lower())
        assert matches >= 2, "Prompt should mention protected package categories"

    def test_session_claude_md_mentions_protected_patterns(self) -> None:
        """Session CLAUDE.md lists protected package patterns."""
        content = build_session_claude_md()

        assert "linux-*" in content or "systemd" in content

    def test_both_prompts_emphasize_valid_toml(self) -> None:
        """Both prompts emphasize that output must be valid TOML."""
        headless = build_headless_prompt("/tmp/scan.json", "/tmp/decisions.toml")
        session_md = build_session_claude_md()

        assert "valid TOML" in headless or "TOML syntax" in headless
        assert "valid TOML" in session_md or "TOML syntax" in session_md

    def test_headless_prompt_includes_example_output(self) -> None:
        """Headless prompt includes example decisions.toml output."""
        headless = build_headless_prompt("/tmp/scan.json", "/tmp/decisions.toml")

        assert "```toml" in headless
        assert "name =" in headless or 'name = "' in headless
        assert "reason =" in headless or 'reason = "' in headless
        assert "confidence =" in headless
        assert "category =" in headless


class TestModuleExports:
    """Tests for module-level exports."""

    def test_prompts_module_exports(self) -> None:
        """prompts module exports expected symbols."""
        from popctl.advisor import prompts

        assert hasattr(prompts, "HEADLESS_PROMPT")
        assert hasattr(prompts, "SESSION_CLAUDE_MD")
        assert hasattr(prompts, "INITIAL_PROMPT")
        assert hasattr(prompts, "CATEGORIES")
        assert hasattr(prompts, "DECISIONS_SCHEMA")
        assert hasattr(prompts, "build_headless_prompt")
        assert hasattr(prompts, "build_session_claude_md")
        assert hasattr(prompts, "build_initial_prompt")
        assert hasattr(prompts, "get_decisions_schema")
        assert hasattr(prompts, "get_prompt_file_path")

    def test_advisor_init_exports_prompts(self) -> None:
        """advisor __init__ exports prompt functions."""
        from popctl.advisor import (
            CATEGORIES,
            build_headless_prompt,
            build_initial_prompt,
            build_session_claude_md,
            get_decisions_schema,
            get_prompt_file_path,
        )

        assert callable(build_headless_prompt)
        assert callable(build_session_claude_md)
        assert callable(build_initial_prompt)
        assert callable(get_decisions_schema)
        assert callable(get_prompt_file_path)
        assert isinstance(CATEGORIES, tuple)
