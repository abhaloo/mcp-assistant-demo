"""
Application configuration via environment variables.

PYTHON CONCEPT: pydantic-settings
- Think of this as a typed, validated version of dotenv
- Similar to how you'd use zod + dotenv in TypeScript
- Reads from .env file automatically
- Validates types at startup — crashes early if config is wrong
"""

from datetime import date
from pathlib import Path
from typing import ClassVar, Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings

from app.crypto.event_keyring import EventEncryptionKeyring

# Load .env into os.environ BEFORE anything imports LangChain.
# pydantic-settings reads .env for its own fields, but LangChain reads
# os.environ directly for tracing config. load_dotenv() bridges the gap.
load_dotenv()

_REDACTED = "***redacted***"

# Substrings that mark a settings field as credential-bearing. Deliberately
# matched on the FIELD NAME, not the value: a name-based rule stays correct
# when a new secret field is added with an unfamiliar value shape, whereas
# value-sniffing (does this look like a key?) silently misses new formats.
_SECRET_NAME_PARTS = (
    "api_key",
    "secret",
    "password",
    "token",
    "credential",
    "_dsn",
    "hmac",
    "tenant_id",
    "client_id",
    "connection_string",
    "encryption_key",
)

# Connection strings embed their password, so the whole value is withheld.
# Matched as suffixes so plain endpoints (base_url, azure_endpoint,
# corpus_blob_account_url, telemetry_otlp_endpoint) stay readable for
# debugging -- those carry no credential.
_SECRET_NAME_SUFFIXES = ("_database_url", "redis_url")


def _is_secret_setting(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in _SECRET_NAME_PARTS) or lowered.endswith(
        _SECRET_NAME_SUFFIXES
    )


class _RedactedRepr:
    """Redact credential values from EVERY rendering of a settings model.

    Pydantic builds ``repr()``, ``str()`` and the ``AttributeError`` message for a
    missing attribute from this method, so redacting here is the only complete fix:
    tracebacks, log records, ``print`` and APM breadcrumbs all format the model
    without asking us first. Field NAMES are preserved so a diagnostic can still see
    whether a value was configured.
    """

    def __repr_args__(self):
        for name, value in super().__repr_args__():
            if name and value and _is_secret_setting(str(name)):
                yield name, _REDACTED
            else:
                yield name, value


class TelemetrySettings(_RedactedRepr, BaseSettings):
    """OpenTelemetry export target. Reads the flat TELEMETRY_* env names."""

    model_config = {
        "env_prefix": "TELEMETRY_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    exporter: Literal["console", "azure_monitor", "otlp"] = "console"
    service_name: str = "mcp-rag-qa"
    environment: str = "development"  # -> resource deployment.environment
    otlp_endpoint: str = "http://localhost:4317"


class HealthSettings(_RedactedRepr, BaseSettings):
    """Readiness probe cadence. Keep the interval well under the probe failure
    budget so a down dependency is detected quickly."""

    model_config = {
        "env_prefix": "HEALTH_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    background_interval_seconds: int = 15
    # Observability only -- surfaced in /readyz, never gating readiness.
    error_window_seconds: int = 60


# Gate C validates the two settings below against these exact values, not
# merely for presence -- a different retention window or role set is a
# missing input, not a smaller pass. Encrypted reasoning-trace content lives
# for this many days; the sole role is the dedicated audited break-glass
# incident-response role -- normal application and support roles see safe
# metadata only.
FROZEN_TRACE_RETENTION_DAYS: int = 30
FROZEN_DECRYPT_ROLES: tuple[str, ...] = ("reasoning_trace_incident_response",)


class Settings(_RedactedRepr, BaseSettings):
    """
    All config lives here. Values come from .env or environment variables.
    Pydantic coerces types automatically (e.g., string "1000" -> int 1000).
    """

    # Models
    base_url: str = ""  # OpenAI/GitHub-Models path only; unused when chat_provider=azure
    model_api_key: str = ""  # OpenAI/GitHub-Models path only; unused when chat_provider=azure
    # Consumed by DefaultAzureCredential via os.environ; declared so pydantic doesn't reject
    # (and echo) them when present in .env.
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""
    embedding_model: str = "text-embedding-3-small"
    chat_model: str = (
        "meta-llama/llama-3.3-70b-instruct:free"  # Cheap for dev. Switch to gpt-4o for prod.
    )
    # SDK default is 2; raised for batch/eval resilience under concurrency. The OpenAI/Azure
    # SDK honors Retry-After + retry-after-ms automatically, so extra retries are bounded
    # waits on rate-limit headers, not blind spins.
    model_max_retries: int = 6
    # Per-request wall-clock for a single LLM call (seconds). The OpenAI/Azure SDK
    # default is 600s; with model_max_retries that lets one stalled completion park a
    # worker for ~70 min with no app-level deadline (and the breaker can't trip — the
    # call never returns). 60s is generous for a single gpt-4o-mini completion.
    model_request_timeout_s: float = 60.0
    # Caps concurrent in-flight LLM-bearing calls (classify/generate/sql/merge/stream),
    # PER WORKER PROCESS (module-level asyncio.Semaphore) — effective ceiling is
    # workers x this. The threadpool bounds the JSON path (~min(32, cpu+4)); the async
    # classify/merge/stream paths are otherwise UNBOUNDED. ~= the thread ceiling is a
    # sane default. ge=1: Semaphore(0) is legal but permanently locked → a 0 would
    # silently deadlock every LLM path, so reject it loudly at startup.
    llm_max_concurrency: int = Field(default=8, ge=1)

    # Retriever backend selection — factory dispatches on this.
    retriever_kind: Literal["chroma", "azure_search"] = "chroma"

    # Parser backend selection — parser_factory dispatches on this.
    # "pypdf" = naive text baseline (current behaviour); "unstructured" = hosted
    # API (ship-fast); "docling" = future self-host arm (not yet implemented).
    parser_kind: Literal["pypdf", "unstructured", "docling"] = "pypdf"
    unstructured_api_key: str = ""  # hosted API key; only needed when parser_kind="unstructured"
    unstructured_api_url: str = ""  # e.g. https://api.unstructuredapp.io

    # ChromaDB
    chroma_persist_dir: str = "./data/chroma"
    collection_name: str = "mcp_documents"

    # Azure AI Search (only required when retriever_kind == "azure_search")
    azure_search_endpoint: str | None = None
    azure_search_index: str = "mcp-rag-qa"

    chat_provider: Literal["openai", "azure"] = "openai"
    # Embedding backend — independent of chat_provider. "offline" is the cloud E2E
    # document-rag seam: deterministic vectors with no provider credentials or spend.
    embedding_provider: Literal["openai", "azure", "offline"] = Field(
        default="openai",
        validation_alias="RAG_EMBEDDING_PROVIDER",
    )
    # Requests a usage chunk on the streamed response (langchain-openai's
    # `stream_usage` -> `stream_options={"include_usage": true}`). Default on: both
    # OpenAI-compatible gateways and Azure (api-version >= 2024-09-01-preview, this
    # repo pins 2024-10-21) support it. Settings-driven so a gateway that rejects the
    # extra field (some OpenAI-compatible proxies 422 on unknown args) can disable it
    # without a code change.
    chat_stream_usage: bool = True
    azure_endpoint: str | None = None  # e.g. https://<name>.services.ai.azure.com
    azure_chat_deployment: str = "gpt-4o-mini"
    # Stronger model for the escalation cascade (hard receivables-aggregation queries).
    # Routed to by app/rag/model_router.py; see docs/insights-log.md (2026-06-26 / 2026-07-31).
    # Human cutover 2026-07-31: gpt-4.1 → gpt-5.6-luna.
    azure_chat_escalation_deployment: str = "gpt-5.6-luna"
    azure_embedding_deployment: str = "text-embedding-3-small"
    azure_api_version: str = "2024-10-21"
    # ARM Management API attestation for active Azure production routes (env-owned).
    # When all three resource identifiers and the allowlist JSON are set, readiness
    # attests every active Azure catalog deployment against ARM before serving traffic.
    azure_subscription_id: str = ""
    azure_openai_resource_group: str = ""
    azure_openai_account: str = ""
    # JSON map: deployment name -> {model_name, model_version, deployment_state,
    # provisioning_state, sku}. Values are deploy-owned — never hard-coded in code.
    azure_deployment_attestation_allowlist_json: str = ""

    # Active catalog route id per purpose, overriding config/production_model_catalog.yaml's
    # purpose_defaults. Empty means "use the catalog default". Validated at startup by
    # app/providers/catalog_startup.py: an id that names no route, or whose target cannot
    # satisfy the purpose, fails the deploy rather than 503-ing at request time.
    ask_model_route_classify: str = ""
    ask_model_route_rag_answer: str = ""
    ask_model_route_sql_agent: str = ""
    ask_model_route_conversation: str = ""
    ask_model_route_record_reasoning: str = ""
    ask_model_route_coordinator: str = ""
    ask_model_route_sql_agent_hard_financial: str = ""

    # Emits allowlisted activity stages on the v2 stream. Off leaves the
    # answer, tables and terminal outcome unchanged.
    ask_activity_events_enabled: bool = True

    # Binds the offer_follow_up tool on the semantic answer model. Off by
    # default: a bound tool changes the production answer call, so an
    # environment switches it on only after the rollout gate in the ledger.
    ask_follow_up_offer_enabled: bool = False

    # Serves the result_page operation. Off fails it closed with its own copy
    # and mints no next-page cursor: the chat panel no longer offers a next
    # page, and the cursor replay defect is not fixed in this slice.
    ask_result_page_enabled: bool = False

    # DeepSeek (capability-gated; optional — unset leaves registry empty)
    deepseek_foundry_deployment: str = ""
    deepseek_direct_base_url: str = ""
    deepseek_direct_api_key: str = ""
    deepseek_api_key: str = ""  # legacy .env alias; use deepseek_direct_api_key
    deepseek_direct_model: str = ""

    # OpenRouter (API key stays on Settings, not ModelSpec)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Direct OpenAI API (GPT-5.6 Luna and other api.openai.com models)
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    # RAG corpus (ingestible company documents — not developer docs/)
    document_rag_enabled: bool = False
    ask_document_fault: Literal["none", "stage_timeout"] = "none"
    ask_tool_layer_enabled: bool = False
    conversation_coordinator_enabled: bool = False
    allow_ask_clock_override: bool = False
    ask_max_deadline_ms: int = 25_000
    ask_planner_step_ceiling_seconds: float | None = 18.0
    ask_turn_unbounded: bool = False
    # SHA-256 of the accepted document-tool profile; enablement needs the file to match.
    ask_tool_layer_profile_sha256: str = ""
    corpus_dir: str = "data/corpus/company"
    corpus_source: Literal["local", "azure_blob"] = "local"
    corpus_blob_account_url: str = ""
    corpus_blob_container: str = "corpus"

    # RAG parameters
    chunk_size: int = 700  # Characters per chunk
    chunk_overlap: int = 100  # Overlap between chunks (preserves context at boundaries)
    excel_rows_per_chunk: int = 20  # data rows per Excel chunk (header repeats in each)
    top_k: int = 3  # Number of chunks to retrieve per query
    md_header_chunking: bool = False  # markdown-header-aware chunking with section metadata

    # Local cross-encoder rerank-as-FILTER (Chroma path; Azure path uses semantic ranker instead).
    rerank_enabled: bool = False
    rerank_model: str = "ms-marco-MiniLM-L-12-v2"
    rerank_candidates: int = 20
    rerank_score_floor: float = 0.0
    rerank_cache_dir: Path = Path("data/flashrank-cache")

    # Azure AI Search query mode. "vector" = pure vector. "hybrid" adds BM25 keyword leg.
    azure_search_query_mode: Literal["vector", "hybrid", "hybrid_semantic"] = "vector"

    # Rollback capability: when False, RAG accepts any run_id up to max_length
    # so a reverted Billing generator does not 422 every ask.
    # Env-driven; requires redeploy to flip — not a hot rollback switch.
    strict_correlation_id: bool = True

    # LangSmith (observability / tracing) — optional; tracing off unless enabled
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "mcp-rag-qa"

    # OpenTelemetry (production tracing)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
    # Deploy profile — distinct from telemetry.environment (bicep sets ENVIRONMENT=production).
    environment: Literal["development", "production"] = "development"
    applicationinsights_connection_string: str | None = None
    sentry_dsn: str = ""  # optional; when set, ask failures are reported to Sentry
    # Sampling is env-driven: the SDK reads OTEL_TRACES_SAMPLER /
    # OTEL_TRACES_SAMPLER_ARG when no explicit sampler is set on the provider.
    # Prod default: OTEL_TRACES_SAMPLER=parentbased_traceidratio ARG=0.15

    # PII
    redaction_enabled: bool = True
    redaction_confidence_threshold: float = 0.4
    redaction_entities: list[str] = ["PHONE_NUMBER"]
    phone_region: str = "TZ"
    redaction_audit_log_path: str = "logs/redactions.jsonl"
    redaction_hmac_key: str  # secret key for HMAC audit log hashing — set in .env

    # SQL DB — placeholder default keeps the engine in app/eval/sql/agent/billing_engine.py from
    # crashing at import when billing is not configured.
    mcp_billing_database_url: str = "mysql+pymysql://placeholder"
    billing_db_read_only: bool = True
    # Structured-access containment gate (Ask AI context/access plan, Phase 0 —
    # docs/superpowers/reports/2026-07-21-ask-ai-context-access-implementation-report.md).
    # "disabled" = feature-off: app/rag/sql_access.ensure_scoped_sql_access() denies
    # every JSON/SSE structured caller unconditionally, regardless of role/permissions.
    # "scoped" is reserved for the entity/department row-policy enforcement a later
    # phase wires behind that same seam. This setting ALONE never re-enables SQL
    # construction, in either value: ensure_scoped_sql_access() also requires an
    # explicit ScopedSqlPolicy object that no production call site constructs today
    # (Phase 6 will) — see app/rag/sql_access.py for the full reasoning. An
    # unrecognized value fails startup via pydantic Literal validation (same
    # convention as every other mode switch in this file).
    sql_policy_mode: Literal["disabled", "scoped"] = "disabled"

    # Query Record store (S1a) — Azure Postgres Flexible Server in prod; compose locally.
    query_record_database_url: str = ""
    query_record_project_id: str = "mcp-default"
    query_record_billing_commit: str = ""
    query_record_rag_commit: str = ""
    query_record_manifest_path: str = ""
    # Secret-manager injected AES-GCM keyring for durable Business Query event payloads.
    # Shape: {"current":"v2","previous":"v1","keys":{"v2":"<base64-32-bytes>",...}}.
    business_query_event_encryption_keys: str = ""
    business_query_event_payload_mode: Literal["digest_only", "encrypted"] = "digest_only"
    business_query_event_retention_days: int = Field(default=30, ge=1, le=365)
    # Global default-off gate for durable raw question text (D3). Audit + RBAC are
    # RELEASABLE gates — not implemented in S1a.
    query_record_raw_capture_enabled: bool = False
    # Eval harness: persist chain-of-thought on campaign answer rows (Campaign
    # Reasoning Persistence). Defaults ON for study; freeze/campaign may override off.
    # Never a production Ask/user-facing feature; do not commit raw jsonl to git.
    persist_eval_reasoning_text: bool = True
    persist_eval_reasoning_raw: bool = True
    # Eval snapshot DB — a frozen local restore of the billing dump used ONLY by the
    # SQL diagnostic harness (scripts/eval/evaluate_sql_agent.py). Never the production tunnel.
    eval_snapshot_database_url: str | None = None
    # Deterministic record-tools DB (Ask AI context/access plan, Phase 5 —
    # docs/superpowers/reports/2026-07-21-ask-ai-context-access-implementation-report.md,
    # "Phase 5 (RAG) — record tools + executor"). None (the default) means the three
    # record tools (search/list/get_business_records) are UNAVAILABLE: nothing in
    # app/policy/record_executor.py constructs a database connection. There is NO
    # fallback to mcp_billing_database_url, ever, in either direction — the two URLs
    # are read independently and app/policy/record_executor.py never references
    # mcp_billing_database_url at all (grep-verified). Setting this ALONE still does
    # not make the tools reachable: app/policy/record_executor.ensure_record_tools_available()
    # also requires a validated v2 record_access principal (entity_id/manifest_hash/
    # resources all populated) — the same "a setting alone cannot create access"
    # discipline as app/rag/sql_access.py's SQL_POLICY_MODE seam (task A1).
    mcp_record_database_url: str | None = None
    # Ceiling for one adapter statement, in seconds. Feeds the record engine's MySQL
    # `SET SESSION max_statement_time` on every pooled connection and the adapter's own
    # wait ceiling; both read this one value, so a connection is never held open by a
    # server-side statement the adapter has already stopped waiting for.
    adapter_statement_timeout_seconds: float = Field(default=10.0, gt=0)
    # Width of the adapter thread pool. Deliberately small and separate from asyncio's
    # shared default executor (min(32, cpu+4) workers, process-wide): a wait_for timeout
    # does not cancel the underlying thread, so repeated timeouts during a slow-database
    # incident would otherwise exhaust the SHARED pool and stall every other to_thread
    # caller in the process, not just this feature.
    adapter_executor_max_workers: int = Field(default=4, gt=0)
    # Business Query Ask wiring gate. disabled = Phase 0 structured 503 containment;
    # shadow = run Module + record, still 503; enabled = map Module outcomes to Ask.
    business_query_mode: Literal["disabled", "shadow", "enabled"] = "disabled"
    # R3 typed-query rollout controls.  Exact-match legacy filtering remains
    # available while the explicit comparison-clause grammar is off; range
    # operators and bounded analytics require their separate canary gates.
    record_filter_schema_v2: bool = False
    record_analytics_mode: Literal["disabled", "count", "grouped"] = "disabled"
    # Eval-only frozen "today". The SQL eval runs against a FROZEN snapshot, so the agent's
    # relative-date resolution must also be frozen or it drifts away from the gold (which hardcodes
    # dates authored against this anchor). Production leaves this unused and resolves live dates.
    eval_today: date = date(2026, 6, 16)
    # Server-side cap for a single SQL statement (MySQL MAX_EXECUTION_TIME hint, ms).
    # 0 disables. Surfaces as a recoverable ToolMessage so the self-correction loop can react.
    sql_agent_statement_timeout_ms: int = 15000
    # Hard caps on sql_db_query tool output returned to the model (rows + serialized chars).
    sql_tool_max_rows: int = 20
    sql_tool_max_chars: int = 12_000
    # Pre-call run budget: max paid LLM invokes and estimated next-prompt tokens (cl100k_base).
    # 16: DeepSeek-direct-high tool loops on multi-hop cases exhausted 8 (bc-84a4e015).
    sql_run_max_llm_calls: int = 28
    sql_run_max_next_prompt_tokens: int = 80_000
    # Cost is known only post-call, so the cumulative stop must leave room for one more
    # call. The 2026-08-08 abort cell ran $0.0030/call vs a $0.0016/call typical -- a 2x
    # spread -- and overshot a $0.05 ceiling by $0.028. 0.7 leaves that headroom.
    sql_run_cost_safety_factor: float = 0.7
    # Slice C: pre-execute EXPLAIN on pre-transform sql_db_query (fail-closed default OFF).
    sql_dry_run_explain: bool = False

    # --- Agent memory loop (docs/superpowers/plans/2026-06-26-agent-memory-loop.md) ---
    # A1: where the eval harness + A2 harvest write structured failure records.
    failure_store_dir: str = "evals/failures"
    # A3: runtime episodic retrieval. OFF by default — gated, and validatable only once
    # production confirmed-failures exist (the store is production-sourced by construction).
    episodic_enabled: bool = False
    episodic_k: int = 2  # exemplars per query (small: FollowRAG/Mu dilution)
    episodic_store_path: str = "evals/failures/episodic_exemplars.json"
    # A3D: always-on definitional RULES block (semantic memory). Independent gate from A3 —
    # a rule can help while exemplars don't, or vice versa. OFF until it passes the transfer test.
    rules_enabled: bool = False
    # Dev smoke-test only — allows loading an eval-sourced episodic store at runtime.
    episodic_allow_eval_source: bool = False

    # Service-to-service auth — shared HS256 JWT minted by Laravel.
    # Required (no default) so a missing signing key fails startup rather than
    # silently disabling verification — same convention as redaction_hmac_key,
    # but a cryptographically INDEPENDENT secret (different purpose, different
    # blast radius, different rotation). Never reuse one value for both.
    # Set in .env.
    rag_jwt_secret: str
    rag_jwt_iss: str = "laravel-billing"  # must match the `iss` Laravel signs
    rag_jwt_aud: str = "rag-service"  # must match the `aud` Laravel signs

    # --- Multi-turn chat sessions ---
    # Plan: docs/superpowers/plans/2026-06-29-multi-turn-chat-sessions.md
    # Server-side, session-scoped conversation transcript in Redis. OFF by default:
    # when off, the request/response shape and behavior are byte-identical to single-shot.
    conversation_enabled: bool = False
    redis_url: str = ""  # required only when conversation_enabled (validated below)
    conversation_ttl_seconds: int = 1800  # sliding session expiry (30 min)
    conversation_absolute_ttl_seconds: int = 14400  # max thread lifetime (4 h), creation-based
    conversation_max_turns: int = (
        6  # max prior turns fed to condense/prompt (short: lost-in-the-middle)
    )
    conversation_history_max_chars: int = 8000  # hard cap on assembled history text

    # Citation-bearing SSE responses are buffered until their source bindings
    # are finalized. Keep disabled through the server-contract rollout; JSON
    # always returns the canonical finalized Answer payload.
    citation_completion_gate: bool = False

    # Ask AI v2 and Gate C readiness
    canary_encryption_ready: bool = False
    gate_b_new_write_ready: bool = False
    zero_plaintext_backfill_receipt: str | None = None
    frozen_trace_retention_days: int | None = FROZEN_TRACE_RETENTION_DAYS
    frozen_decrypt_roles: tuple[str, ...] = FROZEN_DECRYPT_ROLES

    # Health probes
    health: HealthSettings = Field(default_factory=HealthSettings)

    # API
    api_title: str = "Multi Color Printers Q&A API"
    api_version: str = "0.1.0"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @model_validator(mode="after")
    def _alias_deepseek_direct_api_key(self) -> "Settings":
        """Legacy .env name: DEEPSEEK_API_KEY → deepseek_direct_api_key when unset."""
        if not self.deepseek_direct_api_key and self.deepseek_api_key:
            self.deepseek_direct_api_key = self.deepseek_api_key
        return self

    @model_validator(mode="after")
    def _validate_active_mode(self) -> "Settings":
        """Fail at startup, uniformly — not at first request."""
        missing: list[str] = []
        if self.document_rag_enabled:
            if self.embedding_provider != "offline":
                if self.chat_provider == "openai" and not (self.base_url and self.model_api_key):
                    missing.append("base_url/model_api_key (chat_provider=openai)")
                if self.chat_provider == "azure" and not self.azure_endpoint:
                    missing.append("azure_endpoint (chat_provider=azure)")
            if self.retriever_kind == "azure_search" and not self.azure_search_endpoint:
                missing.append("azure_search_endpoint (retriever_kind=azure_search)")
            if self.parser_kind == "unstructured" and not (
                self.unstructured_api_key and self.unstructured_api_url
            ):
                missing.append("unstructured_api_key/url (parser_kind=unstructured)")
            if self.corpus_source == "azure_blob" and not self.corpus_blob_account_url:
                missing.append("corpus_blob_account_url (corpus_source=azure_blob)")
        if self.conversation_enabled and not self.redis_url:
            missing.append("redis_url (conversation_enabled=true)")
        if (
            self.business_query_event_payload_mode == "encrypted"
            and not self.business_query_event_encryption_keys
        ):
            missing.append(
                "BUSINESS_QUERY_EVENT_ENCRYPTION_KEYS (business_query_event_payload_mode=encrypted)"
            )
        elif self.business_query_event_payload_mode == "encrypted":
            EventEncryptionKeyring.parse(self.business_query_event_encryption_keys)
        if (
            self.conversation_enabled
            and self.redis_url
            and self.environment == "production"
            and not self.redis_url.startswith("rediss://")
        ):
            missing.append(
                "redis_url must use TLS (rediss://) when conversation_enabled in production"
            )
        default_deadline_ms = 25_000
        default_planner_ceiling = 18.0
        clocks_non_default = (
            self.ask_max_deadline_ms != default_deadline_ms
            or self.ask_planner_step_ceiling_seconds != default_planner_ceiling
            or self.ask_turn_unbounded
        )
        if clocks_non_default and not self.allow_ask_clock_override:
            missing.append("ALLOW_ASK_CLOCK_OVERRIDE=1 is required to change Ask clocks")
        if missing:
            raise ValueError(f"Config incomplete for active modes: {missing}")
        return self

    @property
    def active_chat_model(self) -> str:
        """Name of the model actually used for generation.

        The OpenAI path uses ``chat_model``; the Azure path uses the deployment
        name. Response labels and GenAI telemetry should report this, not the
        ``chat_model`` default (which is misleading once chat_provider=azure).
        """
        if self.chat_provider == "azure":
            return self.azure_chat_deployment
        return self.chat_model


# Singleton pattern: import this instance everywhere
# PYTHON CONCEPT: module-level instantiation runs once on first import
settings = Settings()


def _database_name(url: str | None) -> str | None:
    """The database a URL names; never its credentials or host. A URL the
    parser rejects is an unknown name, not a failed readiness probe."""
    if not url:
        return None
    try:
        return urlsplit(url).path.rsplit("/", 1)[-1] or None
    except ValueError:
        return None


class EffectiveRouteSettings(BaseModel):
    """The route flags this process runs with, as ``/readyz`` reports them.

    An evaluation labels a route by these values, never by the flag a compose
    file or an environment card intended to set. Database identities appear
    as names only.
    """

    BODY_KEY: ClassVar[str] = "effective_settings"

    conversation_coordinator_enabled: bool
    ask_tool_layer_enabled: bool
    ask_tool_layer_profile_recorded: bool
    document_rag_enabled: bool
    business_query_mode: str
    billing_database: str | None
    record_database: str | None
    ask_max_deadline_ms: int = 25_000
    ask_planner_step_ceiling_seconds: float | None = 18.0
    ask_turn_unbounded: bool = False


def effective_route_settings() -> EffectiveRouteSettings:
    """Snapshot of the route flags the running process holds."""
    planner_ceiling = (
        None if settings.ask_turn_unbounded else settings.ask_planner_step_ceiling_seconds
    )
    return EffectiveRouteSettings(
        conversation_coordinator_enabled=settings.conversation_coordinator_enabled,
        ask_tool_layer_enabled=settings.ask_tool_layer_enabled,
        ask_tool_layer_profile_recorded=bool(settings.ask_tool_layer_profile_sha256.strip()),
        document_rag_enabled=settings.document_rag_enabled,
        business_query_mode=settings.business_query_mode,
        billing_database=_database_name(settings.mcp_billing_database_url),
        record_database=_database_name(settings.mcp_record_database_url),
        ask_max_deadline_ms=settings.ask_max_deadline_ms,
        ask_planner_step_ceiling_seconds=planner_ceiling,
        ask_turn_unbounded=settings.ask_turn_unbounded,
    )
