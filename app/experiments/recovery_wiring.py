"""Offline-safe frozen contract loaders for recovery CLI paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.experiments.core.errors import RagExperimentError
from app.experiments.core.protocol import OracleValidation
from app.experiments.rag_experiment import FrozenRagExperiment
from app.experiments.sql_freeze import FrozenSqlExperiment
from app.experiments.tasks.rag_task import RagOracle, RagTask
from app.experiments.tasks.sql_task import SqlOracle, SqlTask


class _RecoveryStubJudge:
    """Judge placeholder for finalize-only recovery; score paths must not run."""

    is_paid = False

    def __call__(
        self, _rows: list[dict[str, Any]], _config: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], OracleValidation]:
        raise RagExperimentError("recovery must not replay judge")


def load_frozen_rag_from_path(path: Path) -> FrozenRagExperiment:
    """Load a sealed RAG frozen contract from disk without network I/O."""
    if not path.is_file():
        raise RagExperimentError(f"frozen contract file is missing: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RagExperimentError("frozen contract file is unreadable") from exc
    freeze_hash = str(document.get("freeze_hash") or "")
    if not freeze_hash:
        raise RagExperimentError("frozen contract missing freeze_hash")
    return FrozenRagExperiment(freeze_hash=freeze_hash, output_path=path, document=document)


def load_frozen_sql_from_path(path: Path) -> FrozenSqlExperiment:
    """Load a sealed SQL frozen contract from disk without network I/O."""
    if not path.is_file():
        raise RagExperimentError(f"frozen contract file is missing: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RagExperimentError("frozen contract file is unreadable") from exc
    freeze_hash = str(document.get("freeze_hash") or "")
    if not freeze_hash:
        raise RagExperimentError("frozen contract missing freeze_hash")
    return FrozenSqlExperiment(freeze_hash=freeze_hash, output_path=path, document=document)


def _recovery_refusal(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise RagExperimentError("recovery must not replay paid execute paths")


def build_rag_task_for_recovery(frozen: FrozenRagExperiment) -> RagTask:
    """Construct a RagTask that can finalize but refuses answer replay."""
    return RagTask(
        frozen=frozen,
        answer_fn=_recovery_refusal,
        oracle=RagOracle(_RecoveryStubJudge(), frozen.document["judge"]),
    )


def build_sql_task_for_recovery(frozen: FrozenSqlExperiment) -> SqlTask:
    """Construct a SqlTask that can finalize but refuses answer replay."""
    return SqlTask(
        frozen=frozen,
        run_case_fn=_recovery_refusal,
        oracle=SqlOracle(),
    )
