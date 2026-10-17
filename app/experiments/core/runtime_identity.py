"""Exact code and environment identity bound before external calls."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeIdentity:
    """Exact code and environment identity bound before external calls."""

    commit_sha: str
    dirty: bool
    environment_hash: str
    dependency_lock_hash: str
