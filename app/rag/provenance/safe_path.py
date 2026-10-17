"""Same-origin relative path check for server-minted record links."""

from __future__ import annotations

import re

# Exactly one leading `/`; reject `//`, `\`, and ASCII controls.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def is_safe_same_origin_path(url: object) -> bool:
    """Accept a non-empty relative path with exactly one leading slash.

    Rejects protocol-relative URLs (`//host`), backslashes, control characters,
    and non-string input. Used defense-in-depth when emitting/rendering links.
    """
    if not isinstance(url, str) or not url:
        return False
    if not url.startswith("/"):
        return False
    if url.startswith("//"):
        return False
    if "\\" in url:
        return False
    if _CONTROL_RE.search(url):
        return False
    return True
