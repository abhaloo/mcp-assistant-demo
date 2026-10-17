"""Atomic file writes with retries for Windows/OneDrive replace races."""

from __future__ import annotations

import os
from pathlib import Path
from time import sleep
from uuid import uuid4


def _retryable_atomic_replace_error(exc: BaseException) -> bool:
    """True for transient Windows replace races on heartbeat/evidence files."""
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 22:
        return True
    return False


def atomic_write_text(path: Path, text: str, *, attempts: int = 10) -> None:
    """Write via temp+replace with retries for Windows/OneDrive replace races."""
    path.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(attempts):
        temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, path)
            return
        except Exception as exc:
            if not _retryable_atomic_replace_error(exc):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            last_error = exc
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            sleep(0.05 * (2**attempt))
    assert last_error is not None
    raise last_error


def atomic_write_text_exclusive(path: Path, text: str) -> None:
    """Write once via temp; refuse if destination already exists (sealed artifacts)."""
    if path.exists():
        raise FileExistsError(f"refusing to overwrite sealed artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
