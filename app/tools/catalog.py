"""Static tool definitions, admission, and typed dispatch."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
from app.rag.retrieval.document_contracts import DocumentFailure, DocumentSearchResult
from app.tools.contracts import (
    AdmittedSelection,
    BusinessQueryHandler,
    BusinessQueryInvocation,
    CatalogRefusal,
    DocumentHandler,
    DocumentInvocation,
    ToolContext,
    ToolFailure,
    ToolInvocation,
    ToolResult,
)

_TOOL_NAMES = frozenset({"business_query", "document_search"})
_MAX_ADMITTED_TOOLS = 2
_invocation_adapter: TypeAdapter[ToolInvocation] = TypeAdapter(ToolInvocation)


@dataclass(frozen=True)
class ToolDefinition:
    name: Literal["business_query", "document_search"]
    version: Literal[1]
    effect: Literal["read"]
    input_type: type
    output_type: type
    handler: BusinessQueryHandler | DocumentHandler


class ToolCatalog:
    def __init__(
        self,
        *,
        bq_handler: BusinessQueryHandler,
        document_handler: DocumentHandler,
    ) -> None:
        if not callable(bq_handler) or not callable(document_handler):
            raise TypeError("handlers must be callables")
        self._definitions = (
            ToolDefinition(
                name="business_query",
                version=1,
                effect="read",
                input_type=BusinessQueryInvocation,
                output_type=AskBusinessQueryResult,
                handler=bq_handler,
            ),
            ToolDefinition(
                name="document_search",
                version=1,
                effect="read",
                input_type=DocumentInvocation,
                output_type=DocumentSearchResult,
                handler=document_handler,
            ),
        )
        self._bq_handler = bq_handler
        self._document_handler = document_handler

    def admit(self, raw: Sequence[Mapping[str, object]]) -> AdmittedSelection | CatalogRefusal:
        if not raw or len(raw) > _MAX_ADMITTED_TOOLS:
            return CatalogRefusal(code="invalid_selection", invocation_ids=())
        calls: list[ToolInvocation] = []
        for item in raw:
            parsed = self._parse(item)
            if isinstance(parsed, CatalogRefusal):
                return parsed
            calls.append(parsed)
        if len({call.invocation_id for call in calls}) != len(calls):
            return CatalogRefusal(code="invalid_selection", invocation_ids=())
        if len({call.name for call in calls}) != len(calls):
            return CatalogRefusal(code="invalid_selection", invocation_ids=())
        return AdmittedSelection(invocations=tuple(calls))

    async def execute_bq(
        self, call: BusinessQueryInvocation, ctx: ToolContext
    ) -> CommittedBqResult | AskBusinessQueryResult:
        validated = BusinessQueryInvocation.model_validate(call.model_dump())
        return await self._bq_handler(validated, ctx)

    async def execute_document(
        self, call: DocumentInvocation, ctx: ToolContext
    ) -> ToolResult | ToolFailure:
        validated = DocumentInvocation.model_validate(call.model_dump())
        domain = await self._document_handler(validated.arguments, ctx)
        if isinstance(domain, DocumentFailure):
            return ToolFailure(
                invocation_id=validated.invocation_id,
                status=domain.status,
                code=domain.code,
            )
        return ToolResult(invocation_id=validated.invocation_id, value=domain)

    def _parse(self, item: Mapping[str, object]) -> ToolInvocation | CatalogRefusal:
        inv_ids = _invocation_ids(item)
        if item.get("effect") == "write":
            return CatalogRefusal(code="write_forbidden", invocation_ids=inv_ids)
        name = item.get("name")
        if name not in _TOOL_NAMES:
            return CatalogRefusal(code="unknown_tool", invocation_ids=inv_ids)
        version = item.get("version")
        if version is True or isinstance(version, (str, float)):
            return CatalogRefusal(code="unsupported_version", invocation_ids=inv_ids)
        if version != 1:
            return CatalogRefusal(code="unsupported_version", invocation_ids=inv_ids)
        try:
            return _invocation_adapter.validate_python(dict(item))
        except ValidationError:
            return CatalogRefusal(code="invalid_arguments", invocation_ids=inv_ids)


async def dispatch_admitted(
    raw: Sequence[Mapping[str, object]],
    catalog: ToolCatalog,
    ctx: ToolContext,
) -> CatalogRefusal | tuple[AdmittedSelection, ...]:
    admitted = catalog.admit(raw)
    if isinstance(admitted, CatalogRefusal):
        return admitted
    results: list[CommittedBqResult | AskBusinessQueryResult | ToolResult | ToolFailure] = []
    for call in admitted.invocations:
        if call.name == "business_query":
            results.append(await catalog.execute_bq(call, ctx))
        else:
            results.append(await catalog.execute_document(call, ctx))
    return (admitted, *results)


def _invocation_ids(item: Mapping[str, object]) -> tuple[str, ...]:
    invocation_id = item.get("invocation_id")
    if isinstance(invocation_id, str) and invocation_id:
        return (invocation_id,)
    return ()
