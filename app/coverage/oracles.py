"""Which eval cases declare or prove each bundle member or field."""

from __future__ import annotations

import json
from pathlib import Path

from app.coverage.model import OracleProof


def _load_object(path: Path) -> dict[str, object]:
    """Read a JSON file that must hold an object at its top level."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return payload


def _load_list(payload: dict[str, object], key: str, path: Path) -> list[object]:
    """Read a list under key, treating an absent key as empty."""
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"{path}: {key} must be a list")
    return value


def covers_index(suite_path: Path, proof_path: Path | None = None) -> dict[str, OracleProof]:
    """Index eval cases that declare or prove each bundle member or field.

    Args:
        suite_path: Path to the test suite JSON file.
        proof_path: Optional path to a proof JSON file containing passed_case_ids.

    Returns:
        Mapping from member identifier to its OracleProof record.

    Raises:
        ValueError: A payload does not carry the shape this index needs.
    """
    suite = _load_object(suite_path)
    passed: set[str] = set()
    if proof_path is not None:
        proof = _load_object(proof_path)
        passed = {str(i) for i in _load_list(proof, "passed_case_ids", proof_path)}
    declared: dict[str, list[str]] = {}
    for case in _load_list(suite, "cases", suite_path):
        if not isinstance(case, dict) or "id" not in case:
            raise ValueError(f"{suite_path}: every case needs an id")
        covers = case.get("covers", [])
        if not isinstance(covers, list):
            raise ValueError(f"{suite_path}: case {case['id']} covers must be a list")
        for member in covers:
            declared.setdefault(str(member), []).append(str(case["id"]))
    return {
        member: OracleProof(
            member=member,
            declared=sorted(ids),
            proven=sorted(i for i in ids if i in passed),
        )
        for member, ids in declared.items()
    }
