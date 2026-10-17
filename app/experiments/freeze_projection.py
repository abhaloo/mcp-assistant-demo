"""Shared helpers for stamping ArmProfile.freeze_projection() onto frozen arm blobs."""

from __future__ import annotations

# Subset of freeze_projection() keys every frozen arm blob must carry (B1-7).
FREEZE_ARM_BLOB_KEYS = frozenset(
    {
        "harness_output_cap_tokens",
        "attested_output_cap_tokens",
        "deployment_attestation",
        "est_answer_usd",
    }
)


def merge_profile_projection_onto_arm_blob(
    arm_blob: dict[str, object],
    projection: dict[str, object],
) -> dict[str, object]:
    """Stamp profile projection fields onto one frozen arm entry."""
    merged = dict(arm_blob)
    merged["profile_id"] = projection["id"]
    for key in FREEZE_ARM_BLOB_KEYS:
        merged[key] = projection[key]
    return merged
