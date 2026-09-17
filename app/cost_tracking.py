"""Cost tracking for Azure/OpenAI API calls.

Costs are MEASURED, not estimated. Every successful Azure OpenAI / OpenAI
response carries a `usage` field with `prompt_tokens` and `completion_tokens` —
the same counts Azure bills on. Capture is via an httpx response event hook,
which fires regardless of caller (LangChain wrappers, raw OpenAI SDK, RAGAS).

Stage attribution uses a `ContextVar` so the same client can serve multiple
pipeline stages (ingest / retrieval_eval / answer_gen / ragas_eval) without
contaminating each other's totals.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

import httpx

_current_stage: ContextVar[str | None] = ContextVar("stage", default=None)


class CostTracker:
    """Accumulates `usage` tokens grouped by (stage, model) pair.

    Schema:
        {stage_name: {model_name: {input, output, calls}}}

    Single-model-per-stage was a bug: `answer_gen` and `ragas_eval` use BOTH
    chat AND embeddings (chroma_store.similarity_search emits embedding queries
    inside those stages). Storing one model per stage locked the bucket's model
    field to whichever call came FIRST, so cost rollup priced everything at
    that model's rate. With per-(stage, model) tracking the rollup applies
    the right price to each model's tokens.
    """

    def __init__(self) -> None:
        # {stage: {model: {input, output, calls}}}
        self.totals: dict[str, dict[str, dict]] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Set the active stage for the duration of the `with` block.

        Exception-safe: the prior stage is restored even if the inner block
        raises. Nest freely — the ContextVar token mechanism keeps state
        consistent across coroutine and thread boundaries.
        """
        token = _current_stage.set(name)
        try:
            yield
        finally:
            _current_stage.reset(token)

    def _record(self, usage: dict, model: str) -> None:
        """Add a `usage` payload to the active stage's (model) bucket.

        No-op if no stage is active. Buckets are keyed by `model` (the value
        Azure returns in `usage.model` — typically a versioned ID like
        `gpt-4o-mini-2024-07-18`).
        """
        stage = _current_stage.get()
        if not stage:
            return
        by_stage = self.totals.setdefault(stage, {})
        bucket = by_stage.setdefault(model, {"input": 0, "output": 0, "calls": 0})
        bucket["input"] += usage.get("prompt_tokens", 0)
        bucket["output"] += usage.get("completion_tokens", 0)
        bucket["calls"] += 1


def make_tracked_http_client(tracker: CostTracker) -> httpx.Client:
    """Return an `httpx.Client` whose responses are siphoned into `tracker`.

    The hook reads the response body (necessary for `resp.json()`), parses
    the JSON, and records any `usage` field it finds. Failures are swallowed:
    cost bookkeeping must never break the underlying request.
    """

    def hook(resp: httpx.Response) -> None:
        if resp.status_code != 200:
            return
        try:
            resp.read()
            data = resp.json()
            usage = data.get("usage")
            if usage:
                tracker._record(usage, data.get("model", "unknown"))
        except Exception:
            pass

    return httpx.Client(event_hooks={"response": [hook]})
