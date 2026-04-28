"""Path sandbox helpers for file tools."""

import os
from pathlib import Path


def _sandbox_root_from_env() -> Path:
    raw = os.getenv("CORECODER_SANDBOX_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(r"D:\corecodertest").resolve()


SANDBOX_ROOT = _sandbox_root_from_env()

# Supervisor session snapshot (optional); kept under sandbox alongside user files.
DEFAULT_SESSION_SNAPSHOT_NAME = ".session.json"


def sandbox_path(*parts: str) -> Path:
    return SANDBOX_ROOT.joinpath(*parts).resolve()


def ensure_within_sandbox(path: Path) -> str | None:
    """Return error message when path is outside sandbox, else None."""
    p = path.resolve()
    try:
        p.relative_to(SANDBOX_ROOT)
        return None
    except ValueError:
        return (
            f"Access denied: {p} is outside sandbox root {SANDBOX_ROOT}. "
            "Only files under sandbox root are allowed."
        )
