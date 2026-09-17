"""Named preflight asserts for a paid Business Query eval run."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.business_query.definitions import current_bundle
from app.eval.business_query.harness import BUSINESS_DATE, assert_eval_snapshot
from app.telemetry.invocation_ledger import ledger_store_configured

EVAL_ADAPTER_STATEMENT_TIMEOUT_SECONDS = 120.0

PROVIDER_IDENTITY_CHECKS: dict[str, dict[str, str]] = {
    "azure": {"credential_source": "entra", "client_kind": "azure"},
    "deepseek_direct": {"credential_source": "deepseek_direct", "client_kind": "openai_compat"},
    "openrouter": {"credential_source": "openrouter", "client_kind": "openai_compat"},
    "openai": {"credential_source": "openai", "client_kind": "openai_compat"},
}


def fail(message: str, cause: BaseException | None = None) -> None:
    if cause is not None:
        raise SystemExit(f"PREFLIGHT FAIL: {message}") from cause
    raise SystemExit(f"PREFLIGHT FAIL: {message}")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_identity() -> dict[str, object]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if dirty:
        fail("tracked worktree is dirty; commit the exact eval inputs first")
    return {"commit": commit, "tracked_worktree_dirty": False}


def assert_resolver_bundle_coverage(bundle) -> None:
    from app.business_query.plan.value_resolver import assert_resolver_coverage

    try:
        assert_resolver_coverage(bundle)
    except ValueError as exc:
        fail(f"resolver coverage incomplete: {exc}", exc)


def resolve_record_route():
    from app.providers.model_purpose import ModelPurpose
    from app.providers.route_policy import RouteResolutionError, get_route_policy

    try:
        return get_route_policy(reload=True).resolve(ModelPurpose.record_reasoning)
    except RouteResolutionError as exc:
        fail(str(exc), exc)
        raise


def assert_route_matches_expected(
    route,
    *,
    expected_route_id: str | None,
    expected_deployment: str | None,
    expected_provider: str | None,
    expected_reasoning_effort: str | None,
) -> dict[str, str]:
    expected = {
        "route_id": expected_route_id,
        "deployment": expected_deployment,
        "provider": expected_provider,
        "reasoning_effort": expected_reasoning_effort,
    }
    actual = {
        "route_id": route.route_id,
        "deployment": route.deployment,
        "provider": route.provider,
        "reasoning_effort": route.reasoning_effort or "none",
    }
    for field, expected_value in expected.items():
        if expected_value is not None and actual[field] != expected_value:
            fail(f"expected {field} {expected_value!r}, resolved {actual[field]!r}")
    return actual


def construct_record_model():
    from langchain_openai.chat_models.base import BaseChatOpenAI

    from app.providers.capability_model import CapabilityChatModel
    from app.providers.factory import get_chat_model
    from app.providers.model_purpose import ModelPurpose

    try:
        model = get_chat_model(purpose=ModelPurpose.record_reasoning, temperature=0)
    except Exception as exc:  # noqa: BLE001 - convert config failures into a paid-run gate
        fail(f"model construction failed: {exc}", exc)
        raise
    inner = model.inner if isinstance(model, CapabilityChatModel) else model
    if not isinstance(inner, BaseChatOpenAI):
        fail(f"{type(inner).__name__} is not openai-SDK-backed")
    return model, inner


def assert_route_model_deployment(route, model) -> str:
    model_spec = getattr(model, "spec", None)
    deployment = getattr(model_spec, "name", None)
    if deployment != route.deployment:
        fail(
            "route/model deployment mismatch "
            f"({route.route_id} resolved {route.deployment!r}, model has {deployment!r})"
        )
    return deployment


def assert_provider_identity(route, model) -> None:
    model_spec = getattr(model, "spec", None)
    expected = PROVIDER_IDENTITY_CHECKS.get(route.provider)
    if (
        expected is None
        or getattr(model_spec, "credential_source", None) != expected["credential_source"]
        or getattr(model_spec, "client_kind", None) != expected["client_kind"]
    ):
        fail(
            "route/provider/model identity mismatch "
            f"(route={route.provider!r}, client_kind={getattr(model_spec, 'client_kind', None)!r}, "
            f"credential_source={getattr(model_spec, 'credential_source', None)!r})"
        )


def assert_langsmith_disabled(settings) -> None:
    if settings.langsmith_tracing:
        fail("LANGSMITH_TRACING must be false")


def assert_deepseek_direct_identity(route, settings) -> None:
    if route.provider != "deepseek_direct":
        return
    base_url = settings.deepseek_direct_base_url.rstrip("/")
    if base_url != "https://api.deepseek.com":
        fail(f"DeepSeek direct base URL must be https://api.deepseek.com, found {base_url!r}")
    if route.deployment != "deepseek-v4-flash":
        fail(
            "DeepSeek direct must be deepseek-v4-flash "
            f"(v4 flash 0731 family), found {route.deployment!r}"
        )


def assert_openai_direct_base_url(route, model) -> None:
    if route.provider != "openai":
        return
    model_spec = getattr(model, "spec", None)
    base_url = (getattr(model_spec, "api_base", "") or "").rstrip("/")
    if base_url != "https://api.openai.com/v1":
        fail(f"OpenAI-direct base URL must be https://api.openai.com/v1, found {base_url!r}")


def build_arm_record(route, model, deployment: str, settings) -> dict[str, Any]:
    from app.business_query.plan import json_object_protocol_hash, planner_schema_hash

    model_spec = getattr(model, "spec", None)
    return {
        "route_id": route.route_id,
        "deployment": deployment,
        "provider": route.provider,
        "client_kind": getattr(model_spec, "client_kind", None),
        "credential_source": getattr(model_spec, "credential_source", None),
        "wire_model": getattr(model_spec, "model_id", None) or deployment,
        "structured_output_mode": route.structured_output_mode,
        "prompt_adjunct_hash": json_object_protocol_hash()
        if route.structured_output_mode == "json_object"
        else None,
        "schema_hash": planner_schema_hash(route.structured_output_mode),
        "direct_model_env_set": bool(settings.deepseek_direct_model)
        if route.provider == "deepseek_direct"
        else None,
        "reasoning_effort": route.reasoning_effort,
        "request_timeout_s": route.request_timeout_s,
        "provider_retry_budget": route.max_retries,
    }


def inspect_eval_database(engine, db_name: str) -> dict[str, Any]:
    with engine.connect() as conn:
        identity = (
            conn.execute(text("SELECT DATABASE() AS db_name, @@hostname AS host, @@port AS port"))
            .mappings()
            .one()
        )
        view_rows = conn.execute(
            text(
                "SELECT table_name, view_definition FROM information_schema.views "
                "WHERE table_schema = DATABASE() ORDER BY table_name"
            )
        ).all()
        bill_ids = conn.execute(text("SELECT id FROM bills ORDER BY id")).scalars().all()
        semantic = conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.views "
                "WHERE table_schema = DATABASE() AND table_name LIKE 'ai_v1_bq_%'"
            )
        ).scalar()
        views_total = conn.execute(
            text("SELECT COUNT(*) FROM information_schema.views WHERE table_schema = DATABASE()")
        ).scalar()
        bills = conn.execute(text("SELECT COUNT(*) FROM bills")).scalar()
    assert_eval_snapshot(views_total=views_total, semantic_views=semantic, bills=bills)
    if identity["db_name"] != db_name:
        fail(f"connected database is {identity['db_name']!r}, expected {db_name!r}")
    return {
        "identity": identity,
        "view_rows": view_rows,
        "bill_ids": bill_ids,
        "semantic": semantic,
        "views_total": views_total,
        "bills": bills,
    }


def run_eval_preflight(
    engine,
    cases: dict,
    oracle: dict,
    *,
    expected_route_id: str | None,
    expected_deployment: str | None,
    expected_provider: str | None,
    expected_reasoning_effort: str | None,
    db_name: str,
    cases_path: Path,
    oracle_path: Path,
    scoring_specs_path: Path,
) -> dict:
    from app.business_query.composition import module_step_timeout_seconds
    from app.business_query.plan.value_resolver import resolver_coverage
    from app.config import settings
    from app.core.ask_errors import resolve_production_route
    from app.providers.model_purpose import ModelPurpose

    bundle = current_bundle()
    assert_resolver_bundle_coverage(bundle)
    route = resolve_record_route()
    assert_route_matches_expected(
        route,
        expected_route_id=expected_route_id,
        expected_deployment=expected_deployment,
        expected_provider=expected_provider,
        expected_reasoning_effort=expected_reasoning_effort,
    )
    model, inner = construct_record_model()
    deployment = assert_route_model_deployment(route, model)
    assert_provider_identity(route, model)
    assert_langsmith_disabled(settings)
    assert_deepseek_direct_identity(route, settings)
    assert_openai_direct_base_url(route, model)
    arm = build_arm_record(route, model, deployment, settings)
    missing_oracle = [c for c in cases if c not in oracle]
    db = inspect_eval_database(engine, db_name)
    identity = db["identity"]
    info = {
        "repository": repo_identity(),
        "model": f"{type(model).__name__}({type(inner).__name__})",
        "arm": arm,
        "telemetry": {
            "langsmith_tracing": settings.langsmith_tracing,
            "langchain_tracing_v2": os.environ["LANGCHAIN_TRACING_V2"],
        },
        "bundle_hash": bundle.content_hash,
        "resolver_coverage": sorted(resolver_coverage(bundle)),
        "evaluator_hashes": {
            "scorer": sha256_file(Path("app/eval/business_query/scorer.py")),
            "verdict": sha256_file(Path("app/eval/business_query/verdict.py")),
            "holdout": sha256_file(Path("app/eval/business_query/holdout.py")),
            "contract": sha256_file(Path("app/eval/business_query/contract.py")),
        },
        "input_hashes": {
            "cases": sha256_file(cases_path),
            "oracle": sha256_file(oracle_path),
            "scoring_specs": sha256_file(scoring_specs_path),
            "catalog": sha256_file(Path("config/production_model_catalog.yaml")),
            "planner": sha256_file(Path("app/business_query/plan/llm_planner.py")),
        },
        "cases": len(cases),
        "oracle_backed": len(cases) - len(missing_oracle),
        "behaviour_only": sorted(missing_oracle),
        "database": {
            "name": identity["db_name"],
            "host": identity["host"],
            "port": identity["port"],
            "views_fingerprint": hashlib.sha256(
                json.dumps(db["view_rows"], default=str).encode("utf-8")
            ).hexdigest(),
            "bill_id_fingerprint": hashlib.sha256(
                json.dumps(db["bill_ids"], default=str).encode("utf-8")
            ).hexdigest(),
        },
        "views_total": db["views_total"],
        "semantic_views": db["semantic"],
        "bills_visible": db["bills"],
        "business_date": BUSINESS_DATE.isoformat(),
        "eval_timeouts": {
            "module_step_timeout_seconds": module_step_timeout_seconds(
                resolve_production_route(ModelPurpose.record_reasoning)
            ),
            "adapter_statement_timeout_seconds": EVAL_ADAPTER_STATEMENT_TIMEOUT_SECONDS,
        },
        "ledger": "ok" if ledger_store_configured() else "not configured",
    }
    print("PREFLIGHT:", json.dumps(info, indent=2))
    print(f"ledger: {info['ledger']}")
    return info
