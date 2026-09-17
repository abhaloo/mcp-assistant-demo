import pytest

from app.eval.business_query.harness import (
    EVAL_SNAPSHOT,
    assert_eval_snapshot,
    eval_principal,
)
from app.policy.manifest_loader import manifest_content_hash


def test_eval_principal_uses_live_manifest_hash() -> None:
    principal = eval_principal({"id": "bq-01"})
    assert principal.manifest_hash == manifest_content_hash()
    assert principal.manifest_hash.startswith("sha256:")


def test_eval_snapshot_refuses_wrong_view_count() -> None:
    with pytest.raises(SystemExit, match="total views"):
        assert_eval_snapshot(
            views_total=EVAL_SNAPSHOT["views_total"] + 1,
            semantic_views=EVAL_SNAPSHOT["semantic_views"],
            bills=EVAL_SNAPSHOT["bills"],
        )
