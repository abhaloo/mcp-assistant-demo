"""Content-addressed N/N-1 business-definition bundle loading and verification."""

from __future__ import annotations

import importlib.resources
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.business_query.definitions.expression_grammar import _collect_expression_violations
from app.business_query.definitions.schema import (
    _MAX_ACCEPTED_BUNDLES,
    BundleIndex,
    BundleSelectionError,
    BundleValidationError,
    DefinitionBundle,
    InvalidBundleIndexError,
)
from app.models.record_context import compute_record_context_digest
from app.policy.manifest_loader import accepted_manifest_hashes


def get_bundle_dir() -> Path:
    return Path(str(importlib.resources.files("app.business_query") / "bundle"))


_BUNDLE_DIR = get_bundle_dir()
_INDEX_PATH = _BUNDLE_DIR / "index.json"
_BUNDLES_DIR = _BUNDLE_DIR / "bundles"


def _bundle_path(bundles_dir: Path, bundle_hash: str) -> Path:
    hex_digest = bundle_hash.removeprefix("sha256:")
    return bundles_dir / f"{hex_digest}.json"


def _payload_for_digest(raw: dict[str, Any]) -> dict[str, Any]:
    without_hash = {k: v for k, v in raw.items() if k != "content_hash"}
    return without_hash


def _read_and_verify_bundle(bundles_dir: Path, bundle_hash: str) -> DefinitionBundle:
    path = _bundle_path(bundles_dir, bundle_hash)
    if not path.is_file():
        raise InvalidBundleIndexError(
            f"accepted bundle hash {bundle_hash!r} names a bundle file that does not exist: {path}"
        )
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    computed = compute_record_context_digest(_payload_for_digest(raw))
    if computed != bundle_hash:
        raise InvalidBundleIndexError(
            f"bundle {path.name!r} content hash {computed!r} does not match "
            f"its accepted index/filename hash {bundle_hash!r} — tampered or "
            "mismatched bundle file"
        )
    claimed = raw.get("content_hash")
    if claimed != bundle_hash:
        raise InvalidBundleIndexError(
            f"bundle {path.name!r} embedded content_hash {claimed!r} does not match {bundle_hash!r}"
        )
    bundle = DefinitionBundle.model_validate(raw)
    expression_violations = _collect_expression_violations(bundle)
    if expression_violations:
        raise BundleValidationError(
            "bundle expression/schema violations:\n- " + "\n- ".join(expression_violations)
        )
    return bundle


@lru_cache(maxsize=1)
def load_bundle_index() -> BundleIndex:
    with _INDEX_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return BundleIndex.model_validate(raw)


def accepted_bundle_hashes() -> tuple[str, ...]:
    return tuple(load_bundle_index().accepted)


@lru_cache(maxsize=_MAX_ACCEPTED_BUNDLES)
def _cached_bundle(bundle_hash: str) -> DefinitionBundle:
    if bundle_hash not in accepted_bundle_hashes():
        raise InvalidBundleIndexError(f"{bundle_hash!r} is not an accepted bundle hash")
    return _read_and_verify_bundle(_BUNDLES_DIR, bundle_hash)


def load_bundle(bundle_hash: str) -> DefinitionBundle:
    return _cached_bundle(bundle_hash)


def current_bundle() -> DefinitionBundle:
    return load_bundle(load_bundle_index().current)


def _previous_hash(index: BundleIndex) -> str | None:
    others = [h for h in index.accepted if h != index.current]
    return others[0] if others else None


def bundle_for_manifest(manifest_hash: str) -> DefinitionBundle:
    """Pointer order (current, then previous); compatibility list gates each candidate."""
    index = load_bundle_index()
    candidates = [index.current]
    previous = _previous_hash(index)
    if previous is not None:
        candidates.append(previous)

    for candidate_hash in candidates:
        bundle = load_bundle(candidate_hash)
        if manifest_hash in bundle.compatible_policy_manifest_hashes:
            return bundle
    raise BundleSelectionError(
        f"no accepted definition bundle is compatible with manifest hash {manifest_hash!r}"
    )


def bundle_for_declared_hash(bundle_hash: str) -> DefinitionBundle:
    """When a request claims an exact current or accepted-previous bundle hash,
    load that exact bundle. Unknown hashes fail closed without fallback."""
    index = load_bundle_index()
    if bundle_hash not in index.accepted:
        raise BundleSelectionError(
            f"declared bundle hash {bundle_hash!r} is not in accepted bundle hashes "
            f"{index.accepted!r}"
        )
    return load_bundle(bundle_hash)


def verify_bundles_startup() -> BundleIndex:
    """Eager fail-closed validation: every accepted bundle + compatibility intersection."""
    index = load_bundle_index()
    violations: list[str] = []
    accepted_manifests = set(accepted_manifest_hashes())

    for accepted_hash in index.accepted:
        try:
            bundle = load_bundle(accepted_hash)
        except BundleValidationError as exc:
            violations.append(str(exc))
            continue
        except (InvalidBundleIndexError, ValidationError, json.JSONDecodeError, OSError) as exc:
            violations.append(f"{accepted_hash}: {exc}")
            continue

        compat = set(bundle.compatible_policy_manifest_hashes)
        if not (compat & accepted_manifests):
            violations.append(
                f"{accepted_hash}: compatible_policy_manifest_hashes "
                f"{sorted(compat)!r} has empty intersection with accepted "
                f"policy manifest hashes {sorted(accepted_manifests)!r}"
            )

    if violations:
        raise BundleValidationError(
            "definition bundle startup validation failed:\n- " + "\n- ".join(violations)
        )
    return index
