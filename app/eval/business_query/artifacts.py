"""Campaign-artifact publication gate for Business Query acceptance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ArtifactPublicationError(ValueError):
    """Local evidence is not durable acceptance evidence."""


def seal_summary(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sealed = dict(payload)
    sealed["content_sha256"] = hashlib.sha256(encoded).hexdigest()
    return sealed


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def write_sealed_once(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if path.exists():
        raise ArtifactPublicationError(f"resume cannot overwrite {path.as_posix()}")
    sealed = seal_summary(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sealed, indent=2) + "\n", encoding="utf-8")
    return sealed


def assert_acceptance_artifacts_posted(
    *,
    run_kind: str,
    posted_manifest: Path | None,
) -> None:
    if run_kind != "gate":
        raise ArtifactPublicationError(f"{run_kind} cannot emit an acceptance verdict")
    if posted_manifest is None or not posted_manifest.exists():
        raise ArtifactPublicationError(
            "a locally complete run without campaign-artifacts posting cannot emit ACCEPTED"
        )
    try:
        manifest = json.loads(posted_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactPublicationError("campaign-artifacts manifest is unreadable") from exc
    if not isinstance(manifest, dict):
        raise ArtifactPublicationError("campaign-artifacts manifest is unreadable")
    if not manifest.get("posted"):
        raise ArtifactPublicationError("campaign-artifacts manifest is not posted")
    if not manifest.get("summary_sha256") or not manifest.get("verdict_sha256"):
        raise ArtifactPublicationError("posted manifest is missing sealed content hashes")
