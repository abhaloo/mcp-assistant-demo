"""Runtime episodic retrieval (dynamic few-shot) for the SQL agent — A3.

Retrieves the k most similar CONFIRMED PRODUCTION failures (question -> human-verified
gold_sql) and injects them as a few-shot block into the agent's system prompt at build
time. The store is production-sourced and therefore DISJOINT from the dev/holdout eval
sets by construction — so the held-out gate measures real generalization, not benchmark
recall (the v1 design built the store from eval cases and leaked siblings into the gate).

Confirmed + production only. Small k (settings.episodic_k): extra exemplars dilute
instruction-following (FollowRAG 2410.09584; Mu 2502.12197).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import settings
from app.experiments.sql_case_allowlist import FLIP_CANARY_CASE_IDS
from app.paths import REPO_ROOT

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MEMORY_CONTRACT_PATH = REPO_ROOT / "evals" / "experiments" / "sql-post-canary-memory-contract.json"


class EpisodicStoreFile(BaseModel):
    schema_version: int = SCHEMA_VERSION
    source: str = "production"
    embedding_model: str = ""
    dimension: int = 0
    exemplars: list[dict] = Field(default_factory=list)
    vectors: list[list[float]] = Field(default_factory=list)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def load_memory_quarantine_case_ids(*, contract_path: Path | None = None) -> frozenset[str]:
    """7 P0 flip IDs + sealed holdout fold — never written or injected (D9)."""
    path = contract_path or MEMORY_CONTRACT_PATH
    if path.is_file():
        doc = json.loads(path.read_text(encoding="utf-8"))
        flip_ids = tuple(str(x) for x in doc.get("flip_canary_case_ids", FLIP_CANARY_CASE_IDS))
        holdout_ids = tuple(str(x) for x in doc.get("holdout_fold_case_ids", ()))
        return frozenset(flip_ids) | frozenset(holdout_ids)
    return frozenset(FLIP_CANARY_CASE_IDS)


def is_quarantined_case_id(case_id: str | None, *, contract_path: Path | None = None) -> bool:
    if not case_id:
        return False
    return case_id in load_memory_quarantine_case_ids(contract_path=contract_path)


def expected_embedding_dimension() -> int | None:
    """Known dimension for text-embedding-3-small; None if unknown."""
    if settings.embedding_model == "text-embedding-3-small":
        return 1536
    return None


def validate_store_data(data: dict, *, allow_eval_source: bool = False) -> EpisodicStoreFile:
    """Parse and validate serialized store metadata. Raises ValueError on invalid data."""
    if "exemplars" in data and "schema_version" not in data:
        raise ValueError("legacy store format missing schema_version")

    store = EpisodicStoreFile.model_validate(data)

    if store.schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {store.schema_version}")

    if store.source == "eval" and not allow_eval_source:
        raise ValueError("eval-sourced store not allowed in runtime path")

    if len(store.exemplars) != len(store.vectors):
        raise ValueError("exemplar/vector length mismatch")

    if store.vectors:
        dims = {len(v) for v in store.vectors}
        if len(dims) != 1:
            raise ValueError("inconsistent vector dimensions")
        dim = dims.pop()
        expected = expected_embedding_dimension()
        if expected is not None and store.dimension and store.dimension != expected:
            raise ValueError(f"stored dimension {store.dimension} != expected {expected}")
        if expected is not None and dim != expected:
            raise ValueError(f"vector dimension {dim} != expected {expected}")
        if store.dimension and store.dimension != dim:
            raise ValueError("dimension metadata does not match vectors")

    return store


class EpisodicStore:
    def __init__(self, exemplars: list[dict], vectors: list[list[float]]) -> None:
        self._exemplars = exemplars
        self._vectors = vectors

    @classmethod
    def from_failure_dir(cls, path: str | Path, embeddings, *, source: str = "production"):
        """Confirmed, STRUCTURAL records of the given source only (production by default)."""
        records: list[dict] = []
        for fp in sorted(Path(path).glob("*.json")):
            if fp.name.startswith("_") or fp.name == "episodic_exemplars.json":
                continue
            try:
                rec = json.loads(fp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if is_quarantined_case_id(rec.get("case_id")):
                continue
            if (
                rec.get("status") == "confirmed"
                and rec.get("source") == source
                and rec.get("failure_kind") == "structural"
                and rec.get("query_type") != "semantic"
                and rec.get("question")
                and rec.get("gold_sql")
            ):
                records.append(
                    {
                        "case_id": rec.get("case_id"),
                        "question": rec["question"],
                        "gold_sql": rec["gold_sql"],
                    }
                )
        vectors = embeddings.embed_documents([r["question"] for r in records]) if records else []
        return cls(records, vectors)

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        embeddings,
        *,
        allow_eval_source: bool = False,
    ):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        store = validate_store_data(raw, allow_eval_source=allow_eval_source)
        return cls(store.exemplars, store.vectors)

    def to_dict(self, *, source: str = "production") -> dict:
        dim = len(self._vectors[0]) if self._vectors else expected_embedding_dimension() or 0
        return {
            "schema_version": SCHEMA_VERSION,
            "source": source,
            "embedding_model": settings.embedding_model,
            "dimension": dim,
            "exemplars": self._exemplars,
            "vectors": self._vectors,
        }

    def fingerprint(self) -> str:
        """Stable 12-hex id of the store contents — stamped on the SQL span (ADR 0019)."""
        payload = json.dumps([e.get("case_id") for e in self._exemplars], sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    def retrieve_scored(
        self, question: str, k: int, embeddings, *, exclude_case_ids: set[str]
    ) -> list[dict]:
        if not self._exemplars or k <= 0:
            return []
        qv = embeddings.embed_query(question)
        if self._vectors:
            store_dim = len(self._vectors[0])
            if len(qv) != store_dim:
                logger.warning(
                    "query vector dimension %d != store dimension %d", len(qv), store_dim
                )
                return []

        scored: list[tuple[float, dict]] = []
        for ex, v in zip(self._exemplars, self._vectors, strict=True):
            if ex.get("case_id") in exclude_case_ids:
                continue
            score = _cosine(qv, v)
            hit = dict(ex)
            hit["_score"] = score
            scored.append((score, hit))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [ex for _, ex in scored[:k]]


def format_exemplars(exemplars: list[dict]) -> str:
    if not exemplars:
        return ""
    body = "\n\n".join(f"Q: {e['question']}\nSQL: {e['gold_sql']}" for e in exemplars)
    return (
        "\nRETRIEVED EXAMPLES (similar past questions and their verified-correct SQL; "
        "imitate the PATTERN, adapt columns/filters to THIS question):\n" + body + "\n"
    )


def build_episodic_store(
    failure_dir,
    out_path,
    embeddings,
    *,
    source: str = "production",
    human_confirm: bool = False,
) -> int:
    """Precompute the store. source="production" for the live gate; source="eval" ONLY for the
    A3 dev smoke-test (Step 12b) — never the live path. Returns the exemplar count."""
    if source == "eval":
        raise ValueError(
            "refusing to write eval-sourced episodic store — use production confirmed failures"
        )
    if source == "production" and not human_confirm:
        raise ValueError(
            "refusing to write production episodic store without human_confirm=True (D8b)"
        )
    store = EpisodicStore.from_failure_dir(failure_dir, embeddings, source=source)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(store.to_dict(source=source)), encoding="utf-8")
    return len(store.to_dict(source=source)["exemplars"])
