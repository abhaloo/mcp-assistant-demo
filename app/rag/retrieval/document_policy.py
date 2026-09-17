"""Frozen document execution limits from the tool-layer document contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DocumentFault = Literal["none", "stage_timeout"]


@dataclass(frozen=True)
class DocumentExecutionPolicy:
    fault: DocumentFault = "none"
    worker_count: int = 4
    queued_submissions: int = 4
    stage_ceiling_seconds: float = 5.0
    commit_reserve_seconds: float = 2.0
    remote_timeout_seconds: float = 2.0
    remote_retries: int = 0
    chunk_count: int = 3
    max_passage_utf8_bytes: int = 32_768
    max_serialized_utf8_bytes: int = 65_536
