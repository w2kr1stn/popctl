"""Structural typing for the optional djinn-in-a-box session backend.

popctl drives an AI agent either on the host (`claude`/`gemini`/`codex` CLI) or,
when the optional ``[agent]`` extra is installed, inside a djinn-in-a-box session.
The session backend is an optional dependency, so popctl types it against these
local Protocols instead of importing djinn's concrete classes — keeping
``pyright app/`` green whether or not djinn is installed, and decoupling popctl
from djinn's internal type surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class AgentRunResult(Protocol):
    @property
    def success(self) -> bool: ...

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...

    @property
    def returncode(self) -> int: ...


class DjinnSessionManager(Protocol):
    def run_headless(
        self,
        *,
        workspace_dir: Path,
        prompt: str,
        agent: str = ...,
        model: str | None = ...,
        timeout: int = ...,
    ) -> AgentRunResult: ...

    def run_interactive(
        self,
        *,
        workspace_dir: Path,
        agent: str = ...,
        model: str | None = ...,
        initial_prompt: str | None = ...,
    ) -> AgentRunResult: ...
