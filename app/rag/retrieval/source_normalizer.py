"""Normalize source paths so eval cases and retrieved chunks compare apples-to-apples.

Eval cases store `source_file` as `data/corpus/company/<tier>/manuals/<file>.md`.
Loaders put an OS path in chunk metadata. Both sides strip to the last two path components
(`manuals/<file>.md`), which is unique because manual basenames are unique.
"""

from __future__ import annotations

from pathlib import PurePosixPath


def normalize_source(path: str | None) -> str:
    """Return the last `<tier>/<filename>` pair from a source path.

    Returns "" for None / empty / weird-shaped inputs so callers can safely
    compare equality without try/except.
    """
    if not path:
        return ""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return parts[-1] if parts else ""
