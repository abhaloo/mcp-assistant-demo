"""Offline RAG quality gate helpers for the DeepSeek routing experiment."""

from __future__ import annotations

import random

from app.experiments.sql_eval_gate import BOOTSTRAP_SEED


def blind_arm_mapping(arm_labels: list[str], *, seed: int = BOOTSTRAP_SEED) -> dict[str, str]:
    """Shuffle opaque IDs onto arm labels for blinded judging."""
    if len(arm_labels) != len(set(arm_labels)):
        msg = "arm_labels must be unique"
        raise ValueError(msg)
    rng = random.Random(seed)
    opaque_ids = [f"oid-{i:04x}" for i in range(len(arm_labels))]
    pairs = list(zip(opaque_ids, arm_labels, strict=True))
    rng.shuffle(pairs)
    return {oid: arm for oid, arm in pairs}
