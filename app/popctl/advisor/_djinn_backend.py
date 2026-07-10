# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false
"""Runtime loader for the optional djinn-in-a-box session backend.

djinn-in-a-box is an optional dependency (the ``[agent]`` extra). It is imported
lazily here and typed against the local :mod:`popctl.advisor.session_protocol`
Protocols, so the rest of the codebase stays fully typed and ``pyright app/``
is green whether or not djinn is installed. The few djinn-specific rules are
disabled for this isolated module only (file-level comment above).
"""

from __future__ import annotations

import logging
from typing import cast

from popctl.advisor.session_protocol import DjinnSessionManager


def get_session_manager() -> DjinnSessionManager | None:
    try:
        from djinn_in_a_box.core.session import SessionManager
    except ModuleNotFoundError as e:
        if e.name == "djinn_in_a_box":
            return None
        logging.getLogger(__name__).warning(
            "Unable to load optional djinn-in-a-box session backend: %s", e,
        )
        return None
    except ImportError as e:
        logging.getLogger(__name__).warning(
            "Unable to load optional djinn-in-a-box session backend: %s", e,
        )
        return None
    return cast(DjinnSessionManager, SessionManager("popctl"))
