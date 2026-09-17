"""Eval-only ASGI launcher that observes execution seams before serving.

Attaches wrap at bound aliases. Does not change routes, permissions, or results.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.telemetry.invocation_ledger import register_event_loop
from scripts.eval.conversation_execution_capture import ExecutionCapture

BINDING_SITES: dict[str, tuple[tuple[str, str], ...]] = {
    "coordinator_decision": (
        ("app.conversation.coordinator.runtime", "run_coordinator_turn"),
        ("app.services.ask_coordinator", "run_coordinator_turn"),
        ("app.conversation.coordinator.graph", "decide_node"),
        ("app.conversation.coordinator.model", "ProviderCoordinatorModel.decide"),
    ),
    "answer_generation": (("app.services.coordinator_answer", "publish_answer_draft"),),
    "evidence_restore": (
        ("app.api.restore_evidence", "restore_evidence"),
        ("app.conversation.evidence.service", "EvidenceRestoreService.restore"),
        ("app.conversation.followup_context", "restore_selected"),
        ("app.services.coordinator_tools", "restore_selected"),
        ("app.services.coordinator_tools", "AskCoordinatorTools.explain_sources"),
    ),
    "planner": (
        ("app.business_query.wire.planning_round", "run_planner_round"),
        ("app.business_query.wire.module", "run_planner_round"),
    ),
    "business_sql": (
        ("app.business_query.compile.adapter_execution", "execute_internal_plan"),
        # adapter.py binds the name at import time; the SQL call goes through this alias.
        ("app.business_query.compile.adapter", "execute_internal_plan"),
        ("app.policy.record_executor", "PolicyScopedRecordExecutor._execute"),
    ),
    "document_retrieval": (
        ("app.rag.retrieval.document_execution", "DocumentExecutor.execute"),
        ("app.rag.chains.document_chain", "retrieve_and_redact"),
        ("app.services.tool_composition", "retrieve_and_redact"),
    ),
    "record_rehydration": (
        ("app.conversation.rehydration_service", "rehydrate_for_followup"),
        ("app.conversation.turn", "rehydrate_for_followup"),
    ),
    "condense": (
        ("app.conversation.condense", "condense_question"),
        ("app.conversation.turn", "condense_question"),
    ),
    "snapshot_io": (
        ("app.conversation.evidence.store", "SqlAlchemyEvidenceSnapshotStore.put"),
        ("app.conversation.evidence.store", "SqlAlchemyEvidenceSnapshotStore.get"),
    ),
    "transcript_io": (
        ("app.conversation.transcript_store", "InMemoryConversationStore.load"),
        ("app.conversation.transcript_store", "InMemoryConversationStore.append"),
        ("app.conversation.transcript_store", "InMemoryConversationStore.replace_latest_exchange"),
        ("app.conversation.transcript_store", "RedisConversationStore.load"),
        ("app.conversation.transcript_store", "RedisConversationStore.append"),
        ("app.conversation.transcript_store", "RedisConversationStore.replace_latest_exchange"),
    ),
}


@dataclass(frozen=True)
class BindingInventory:
    sites: dict[str, tuple[tuple[str, str], ...]]
    hash: str
    attached: tuple[str, ...]
    restores: tuple[tuple[Any, str, Any], ...]


def _binding_hash(sites: dict[str, tuple[tuple[str, str], ...]]) -> str:
    blob = json.dumps(sites, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _split_attr(owner: Any, dotted: str) -> tuple[Any, str]:
    parts = dotted.split(".")
    target = owner
    for part in parts[:-1]:
        target = getattr(target, part)
    return target, parts[-1]


def install_observers(
    capture: ExecutionCapture,
    *,
    sites: dict[str, tuple[tuple[str, str], ...]] | None = None,
) -> BindingInventory:
    chosen = BINDING_SITES if sites is None else sites
    attached: list[str] = []
    restores: list[tuple[Any, str, Any]] = []
    for operation, aliases in chosen.items():
        for module_name, attr in aliases:
            module = importlib.import_module(module_name)
            owner, name = _split_attr(module, attr)
            original = getattr(owner, name)
            restores.append((owner, name, original))
            setattr(owner, name, capture.wrap(operation, original))
            attached.append(f"{module_name}:{attr}")
    restores.extend(_install_lifecycle(capture, attached))
    return BindingInventory(
        sites=chosen,
        hash=_binding_hash(chosen),
        attached=tuple(attached),
        restores=tuple(restores),
    )


def _install_lifecycle(
    capture: ExecutionCapture, attached: list[str]
) -> list[tuple[Any, str, Any]]:
    life_mod = importlib.import_module("app.conversation.coordinator.action_lifecycle")
    runtime_mod = importlib.import_module("app.conversation.coordinator.runtime")
    lifecycle = life_mod.ActionLifecycle
    stopped = runtime_mod.Stopped
    restores = [
        (lifecycle, "admit", lifecycle.admit),
        (lifecycle, "complete", lifecycle.complete),
        (stopped, "__init__", stopped.__init__),
    ]
    lifecycle.admit = capture.wrap_admit(lifecycle.admit)
    lifecycle.complete = capture.wrap_complete(lifecycle.complete)
    stopped.__init__ = capture.wrap_stopped_init(stopped.__init__)
    attached.extend(
        [
            "app.conversation.coordinator.action_lifecycle:ActionLifecycle.admit",
            "app.conversation.coordinator.action_lifecycle:ActionLifecycle.complete",
            "app.conversation.coordinator.runtime:Stopped.__init__",
        ]
    )
    return restores


def restore_observers(inventory: BindingInventory) -> None:
    for owner, name, original in inventory.restores:
        setattr(owner, name, original)


def observed_route_paths(app: FastAPI) -> list[str]:
    paths = [r.path for r in app.routes if isinstance(r, APIRoute)]
    return sorted(paths)


def wrap_existing_app(
    app: FastAPI,
    capture: ExecutionCapture,
    *,
    sites: dict[str, tuple[tuple[str, str], ...]] | None = None,
) -> BindingInventory:
    inventory = install_observers(capture, sites=sites)
    app.state.canary_capture = capture
    app.state.canary_binding_hash = inventory.hash
    return inventory


def load_instrumented_app(
    capture_path: Path,
    *,
    session_id: str | None = None,
    build_identity: str | None = None,
) -> tuple[FastAPI, ExecutionCapture, BindingInventory]:
    capture = ExecutionCapture.open(
        capture_path,
        session_id=session_id or uuid.uuid4().hex,
        build_identity=build_identity or "canary",
        max_workers=1,
        worker_id=0,
    )
    inventory = install_observers(capture)
    capture.record("session", "binding", binding_hash=inventory.hash, max_workers=1)
    app_mod = importlib.import_module("app.main")
    app = app_mod.app
    app.state.canary_capture = capture
    app.state.canary_binding_hash = inventory.hash
    try:
        register_event_loop()
    except RuntimeError:
        pass
    return app, capture, inventory


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the RAG app with canary capture.")
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--build-identity", default="")
    parser.add_argument("--serve", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    app, capture, inventory = load_instrumented_app(
        args.capture,
        session_id=args.session_id or None,
        build_identity=args.build_identity or None,
    )
    print(json.dumps({"binding_hash": inventory.hash, "session_id": capture.session_id}))
    if not args.serve:
        capture.close()
        return 0
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)
    capture.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
