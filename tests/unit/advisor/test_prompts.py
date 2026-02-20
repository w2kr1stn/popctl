"""Unit tests for prompt templates and builders.

Tests for the advisor prompts module that provides prompt templates for
AI-assisted package classification in workspace-based session modes.
"""

import re

from popctl.advisor.prompts import (
    CATEGORIES,
    INITIAL_PROMPT,
    SESSION_CLAUDE_MD,
    build_session_claude_md,
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
        assert "Interactive Triage" in SESSION_CLAUDE_MD

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
        assert "snapd" in SESSION_CLAUDE_MD
        assert "core*" in SESSION_CLAUDE_MD

    def test_session_claude_md_has_snap_output_section(self) -> None:
        """SESSION_CLAUDE_MD includes [packages.snap] in output format."""
        assert "[packages.snap]" in SESSION_CLAUDE_MD

    def test_session_claude_md_emphasizes_lean_host(self) -> None:
        """SESSION_CLAUDE_MD emphasizes lean host philosophy."""
        assert "lean" in SESSION_CLAUDE_MD.lower()
        assert "removal" in SESSION_CLAUDE_MD.lower()

    def test_session_claude_md_has_all_phases(self) -> None:
        """SESSION_CLAUDE_MD contains all 6 phases (0-5)."""
        assert "Phase 0" in SESSION_CLAUDE_MD
        assert "Phase 1" in SESSION_CLAUDE_MD
        assert "Phase 2" in SESSION_CLAUDE_MD
        assert "Phase 3" in SESSION_CLAUDE_MD
        assert "Phase 4" in SESSION_CLAUDE_MD
        assert "Phase 5" in SESSION_CLAUDE_MD

    def test_session_claude_md_mentions_ask_user_question(self) -> None:
        """SESSION_CLAUDE_MD instructs use of AskUserQuestion tool."""
        assert "AskUserQuestion" in SESSION_CLAUDE_MD

    def test_session_claude_md_mentions_memory(self) -> None:
        """SESSION_CLAUDE_MD references memory.md file."""
        assert "memory.md" in SESSION_CLAUDE_MD

    def test_session_claude_md_has_session_close(self) -> None:
        """SESSION_CLAUDE_MD has formal session close instructions."""
        assert "Session abgeschlossen" in SESSION_CLAUDE_MD


class TestInitialPrompt:
    """Tests for the INITIAL_PROMPT constant."""

    def test_initial_prompt_is_string(self) -> None:
        """INITIAL_PROMPT is a non-empty string."""
        assert isinstance(INITIAL_PROMPT, str)
        assert len(INITIAL_PROMPT) > 0

    def test_initial_prompt_mentions_scan_json(self) -> None:
        """INITIAL_PROMPT references scan.json."""
        assert "scan.json" in INITIAL_PROMPT

    def test_initial_prompt_mentions_phase_0(self) -> None:
        """INITIAL_PROMPT directs agent to start with Phase 0."""
        assert "Phase 0" in INITIAL_PROMPT

    def test_initial_prompt_mentions_claude_md(self) -> None:
        """INITIAL_PROMPT references CLAUDE.md."""
        assert "CLAUDE.md" in INITIAL_PROMPT

    def test_initial_prompt_references_memory(self) -> None:
        """INITIAL_PROMPT mentions memory.md."""
        assert "memory.md" in INITIAL_PROMPT


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
        assert "[packages.snap]" in result

    def test_build_session_claude_md_has_protected_packages(self) -> None:
        """build_session_claude_md lists protected package patterns."""
        result = build_session_claude_md()

        assert "linux-*" in result
        assert "systemd" in result
        assert "snapd" in result
        assert "core*" in result


class TestModuleExports:
    """Tests for module-level exports."""

    def test_prompts_module_exports(self) -> None:
        """prompts module exports expected symbols."""
        from popctl.advisor import prompts

        assert hasattr(prompts, "SESSION_CLAUDE_MD")
        assert hasattr(prompts, "INITIAL_PROMPT")
        assert hasattr(prompts, "CATEGORIES")
        assert hasattr(prompts, "build_session_claude_md")

    def test_advisor_init_exports_prompts(self) -> None:
        """advisor __init__ exports prompt functions."""
        from popctl.advisor import (
            CATEGORIES,
            build_session_claude_md,
        )

        assert callable(build_session_claude_md)
        assert isinstance(CATEGORIES, tuple)
