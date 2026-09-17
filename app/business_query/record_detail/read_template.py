"""Shared read-path primitive for record_detail SQL adapters.

Every adapter in this package binds a caller-supplied list of parent IDs
into an `id IN (...)` clause. Before doing so it dedupes the list (preserving
first-seen order) and caps it, so one request can't fan out into an
unbounded `IN` clause. This module gives that operation one name and one
cap value instead of each adapter re-deriving it.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence

MAX_PARENT_IDS = 20


def cap_ids(ids: Sequence[int | str], *, cap: int = MAX_PARENT_IDS) -> list[int | str]:
    """Dedupe `ids` preserving first-seen order, then cap to at most `cap` entries."""
    return list(OrderedDict.fromkeys(ids))[:cap]
