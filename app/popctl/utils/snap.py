def is_runtime_snap(name: str, notes: str) -> bool:
    """Return whether a Snap is runtime infrastructure rather than an application."""
    if notes in {"base", "snapd"} or name in {"snapd", "bare"} or name.startswith("core"):
        return True
    return name.startswith("gnome-") and name.endswith("-platform")
