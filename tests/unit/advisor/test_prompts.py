"""Unit tests for prompt templates and builders.

Tests for the advisor prompts module that provides prompt templates for
AI-assisted package classification in both headless and interactive modes.
"""

import re
from pathlib import Path

from popctl.advisor.prompts import (
    CATEGORIES,
    DECISIONS_SCHEMA,
    HEADLESS_PROMPT,
    INTERACTIVE_INSTRUCTIONS,
    build_headless_prompt,
    build_interactive_instructions,
    get_decisions_schema,
    get_instructions_file_path,
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


class TestInteractiveInstructionsTemplate:
    """Tests for the INTERACTIVE_INSTRUCTIONS template string."""

    def test_interactive_instructions_is_markdown(self) -> None:
        """INTERACTIVE_INSTRUCTIONS is valid Markdown with headers."""
        assert "# " in INTERACTIVE_INSTRUCTIONS
        assert "## " in INTERACTIVE_INSTRUCTIONS

    def test_interactive_instructions_has_placeholders(self) -> None:
        """INTERACTIVE_INSTRUCTIONS has required format placeholders."""
        assert "{scan_json_path}" in INTERACTIVE_INSTRUCTIONS
        assert "{manifest_path}" in INTERACTIVE_INSTRUCTIONS
        assert "{decisions_output_path}" in INTERACTIVE_INSTRUCTIONS
        assert "{categories}" in INTERACTIVE_INSTRUCTIONS
        assert "{timestamp}" in INTERACTIVE_INSTRUCTIONS

    def test_interactive_instructions_has_classification_guidelines(self) -> None:
        """INTERACTIVE_INSTRUCTIONS contains classification guidelines."""
        assert "KEEP" in INTERACTIVE_INSTRUCTIONS
        assert "REMOVE" in INTERACTIVE_INSTRUCTIONS
        assert "ASK" in INTERACTIVE_INSTRUCTIONS

    def test_interactive_instructions_has_workflow(self) -> None:
        """INTERACTIVE_INSTRUCTIONS describes the workflow."""
        assert (
            "Workflow" in INTERACTIVE_INSTRUCTIONS or "workflow" in INTERACTIVE_INSTRUCTIONS.lower()
        )

    def test_interactive_instructions_mentions_files(self) -> None:
        """INTERACTIVE_INSTRUCTIONS mentions input/output files."""
        assert "scan.json" in INTERACTIVE_INSTRUCTIONS
        assert "manifest" in INTERACTIVE_INSTRUCTIONS.lower()
        assert "decisions.toml" in INTERACTIVE_INSTRUCTIONS


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


class TestBuildInteractiveInstructions:
    """Tests for build_interactive_instructions function."""

    def test_build_interactive_includes_all_paths(self) -> None:
        """build_interactive_instructions includes all file paths."""
        instructions = build_interactive_instructions(
            "/tmp/exchange/scan.json",
            "~/.config/popctl/manifest.toml",
            "/tmp/exchange/decisions.toml",
        )

        assert "/tmp/exchange/scan.json" in instructions
        assert "~/.config/popctl/manifest.toml" in instructions
        assert "/tmp/exchange/decisions.toml" in instructions

    def test_build_interactive_includes_categories(self) -> None:
        """build_interactive_instructions includes categories."""
        instructions = build_interactive_instructions(
            "/tmp/scan.json",
            "/tmp/manifest.toml",
            "/tmp/decisions.toml",
        )

        # Categories should be formatted with backticks
        for category in CATEGORIES:
            assert f"`{category}`" in instructions

    def test_build_interactive_includes_timestamp(self) -> None:
        """build_interactive_instructions includes a timestamp."""
        instructions = build_interactive_instructions(
            "/tmp/scan.json",
            "/tmp/manifest.toml",
            "/tmp/decisions.toml",
        )

        timestamp_pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
        assert re.search(timestamp_pattern, instructions) is not None

    def test_build_interactive_is_markdown(self) -> None:
        """build_interactive_instructions returns valid Markdown."""
        instructions = build_interactive_instructions(
            "/tmp/scan.json",
            "/tmp/manifest.toml",
            "/tmp/decisions.toml",
        )

        # Should have Markdown headers
        assert instructions.startswith("#")
        assert "## " in instructions
        assert "### " in instructions

    def test_build_interactive_has_table(self) -> None:
        """build_interactive_instructions includes a Markdown table."""
        instructions = build_interactive_instructions(
            "/tmp/scan.json",
            "/tmp/manifest.toml",
            "/tmp/decisions.toml",
        )

        # Markdown table syntax
        assert "| File |" in instructions or "|---" in instructions

    def test_build_interactive_mentions_conservative_approach(self) -> None:
        """build_interactive_instructions emphasizes conservative classification."""
        instructions = build_interactive_instructions(
            "/tmp/scan.json",
            "/tmp/manifest.toml",
            "/tmp/decisions.toml",
        )

        assert "conservative" in instructions.lower()


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

    def test_get_instructions_file_path(self, tmp_path: Path) -> None:
        """get_instructions_file_path returns correct path."""
        result = get_instructions_file_path(tmp_path)

        assert result == tmp_path / "instructions.md"
        assert result.name == "instructions.md"

    def test_path_helpers_with_nested_path(self, tmp_path: Path) -> None:
        """Path helpers work with nested directories."""
        exchange_dir = tmp_path / "nested" / "exchange"

        prompt_path = get_prompt_file_path(exchange_dir)
        instructions_path = get_instructions_file_path(exchange_dir)

        assert prompt_path.parent == exchange_dir
        assert instructions_path.parent == exchange_dir


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

    def test_interactive_instructions_mentions_protected_patterns(self) -> None:
        """Interactive instructions list protected package patterns."""
        instructions = build_interactive_instructions(
            "/tmp/scan.json",
            "/tmp/manifest.toml",
            "/tmp/decisions.toml",
        )

        # Should explicitly list some protected patterns
        assert "linux-*" in instructions or "systemd" in instructions

    def test_both_prompts_emphasize_valid_toml(self) -> None:
        """Both prompts emphasize that output must be valid TOML."""
        headless = build_headless_prompt("/tmp/scan.json", "/tmp/decisions.toml")
        interactive = build_interactive_instructions(
            "/tmp/scan.json",
            "/tmp/manifest.toml",
            "/tmp/decisions.toml",
        )

        assert "valid TOML" in headless or "TOML syntax" in headless
        assert "valid TOML" in interactive or "TOML syntax" in interactive

    def test_prompts_include_example_output(self) -> None:
        """Both prompts include example decisions.toml output."""
        headless = build_headless_prompt("/tmp/scan.json", "/tmp/decisions.toml")
        interactive = build_interactive_instructions(
            "/tmp/scan.json",
            "/tmp/manifest.toml",
            "/tmp/decisions.toml",
        )

        # Both should have toml code blocks with examples
        assert "```toml" in headless
        assert "```toml" in interactive

        # Examples should show the structure
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
        assert hasattr(prompts, "INTERACTIVE_INSTRUCTIONS")
        assert hasattr(prompts, "CATEGORIES")
        assert hasattr(prompts, "DECISIONS_SCHEMA")
        assert hasattr(prompts, "build_headless_prompt")
        assert hasattr(prompts, "build_interactive_instructions")
        assert hasattr(prompts, "get_decisions_schema")
        assert hasattr(prompts, "get_prompt_file_path")
        assert hasattr(prompts, "get_instructions_file_path")

    def test_advisor_init_exports_prompts(self) -> None:
        """advisor __init__ exports prompt functions."""
        from popctl.advisor import (
            CATEGORIES,
            build_headless_prompt,
            build_interactive_instructions,
            get_decisions_schema,
            get_instructions_file_path,
            get_prompt_file_path,
        )

        # Just verify imports work
        assert callable(build_headless_prompt)
        assert callable(build_interactive_instructions)
        assert callable(get_decisions_schema)
        assert callable(get_prompt_file_path)
        assert callable(get_instructions_file_path)
        assert isinstance(CATEGORIES, tuple)
