"""SQL agent memory assembly for eval cases — rules and episodic examples."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.eval.sql.episodic import EpisodicStore, format_exemplars, load_memory_quarantine_case_ids
from app.eval.sql.rules_channel import (
    build_rules_block,
    load_definitional_rules,
    rules_fingerprint,
)
from app.providers import get_embeddings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SqlMemoryContext:
    rules_block: str = ""
    rules_fingerprint: str | None = None
    episodic_block: str = ""
    episodic_fingerprint: str | None = None
    exemplar_ids: list[str] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)

    @property
    def block_chars(self) -> int:
        return len(self.rules_block) + len(self.episodic_block)


@dataclass
class _CachedValue:
    path: str
    mtime_ns: int | None
    value: object


_episodic_cache: _CachedValue | None = None


def _load_episodic_store_cached() -> EpisodicStore | None:
    global _episodic_cache
    # Episodic retrieval is an embedding-backed document capability. In the
    # structured-only deployment it must be inert, even when an old config
    # leaves episodic_enabled on; otherwise merely assembling SQL memory would
    # construct a disabled provider before the request reaches SQL.
    if not settings.episodic_enabled or not settings.document_rag_enabled:
        return None

    store_path = Path(settings.episodic_store_path)
    if not store_path.exists():
        key = str(store_path.resolve())
        mtime_ns: int | None = None
        if _episodic_cache is not None and _episodic_cache.path == key:
            return None
        _episodic_cache = _CachedValue(path=key, mtime_ns=mtime_ns, value=None)
        return None

    try:
        mtime_ns = store_path.stat().st_mtime_ns
    except OSError:
        return None

    key = str(store_path.resolve())
    flags_key = f"{settings.episodic_allow_eval_source}"

    if (
        _episodic_cache is not None
        and _episodic_cache.path == f"{key}:{flags_key}"
        and _episodic_cache.mtime_ns == mtime_ns
    ):
        store = _episodic_cache.value
        return store if isinstance(store, EpisodicStore) else None

    try:
        store = EpisodicStore.from_file(
            store_path,
            get_embeddings(),
            allow_eval_source=settings.episodic_allow_eval_source,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        logger.warning("episodic store invalid or unreadable: %s", exc)
        _episodic_cache = _CachedValue(path=f"{key}:{flags_key}", mtime_ns=mtime_ns, value=None)
        return None

    _episodic_cache = _CachedValue(path=f"{key}:{flags_key}", mtime_ns=mtime_ns, value=store)
    return store


def _episodic_exclude_case_ids(
    *, case_id: str | None = None, exclude_fold: frozenset[str] = frozenset()
) -> set[str]:
    """Quarantine blocklist + eval fold exclusion + self-case (D9 read path)."""
    exclude = set(load_memory_quarantine_case_ids()) | set(exclude_fold)
    if case_id:
        exclude.add(case_id)
    return exclude


class EvalSqlMemory:
    def for_eval_case(
        self, case: dict, *, exclude_fold: frozenset[str] = frozenset()
    ) -> SqlMemoryContext:
        case_id = case["id"]
        exclude = _episodic_exclude_case_ids(case_id=case_id, exclude_fold=exclude_fold)
        rules: list[str] = []
        fp: str | None = None

        if settings.rules_enabled:
            failure_dir = Path(settings.failure_store_dir)
            if failure_dir.is_dir():
                rules = load_definitional_rules(failure_dir, exclude_case_ids=exclude)
                fp = rules_fingerprint(rules) if rules else None

        rules_block = build_rules_block(rules)

        store = _load_episodic_store_cached()
        episodic_block = ""
        episodic_fp: str | None = None
        exemplar_ids: list[str] = []
        scores: list[float] = []

        if store is not None:
            emb = get_embeddings()
            hits = store.retrieve_scored(
                case["question"],
                settings.episodic_k,
                emb,
                exclude_case_ids=exclude,
            )
            episodic_block = format_exemplars(hits)
            episodic_fp = store.fingerprint()
            exemplar_ids = [h["case_id"] for h in hits]
            scores = [h["_score"] for h in hits]

        return SqlMemoryContext(
            rules_block=rules_block,
            rules_fingerprint=fp,
            episodic_block=episodic_block,
            episodic_fingerprint=episodic_fp,
            exemplar_ids=exemplar_ids,
            scores=scores,
        )


def clear_memory_caches() -> None:
    """Reset module caches — for tests."""
    global _episodic_cache
    _episodic_cache = None
