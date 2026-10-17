"""Case schema and observation shape for the production-ask quality suite.

Case data itself lives under ``evals/prod_ask/``. This module owns only the
types and the loader, so no eval case id or gold literal enters an application
module (ADR 0047 invariant 12).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.schemas import Answer, QueryType

Dimension = Literal["accuracy", "citations", "rich_text", "follow_ups", "helpfulness"]
RunMode = Literal["stub", "replay", "live"]
Transport = Literal["json", "sse"]

DIMENSIONS: tuple[Dimension, ...] = (
    "accuracy",
    "citations",
    "rich_text",
    "follow_ups",
    "helpfulness",
)


class PrincipalSpec(BaseModel):
    """The caller a case runs as. Permissions drive the retrieval tier filter."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    role: str = Field(min_length=1)
    permissions: list[str] = Field(default_factory=list)
    resources: dict[str, list[str]] | None = Field(
        default=None,
        description=(
            "v2 record_access grants, resource -> actions. Permissions alone "
            "describe a v1 caller, and the route reads THIS snapshot for every "
            "record authorization decision -- a case that omits it cannot "
            "exercise any behaviour that branches on reachability."
        ),
    )
    document_tiers: list[str] = Field(default_factory=list)


class OracleRef(BaseModel):
    """Where the truth for this case comes from, independent of the ask route."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["corpus", "sql", "none"]
    corpus_file: str | None = None
    locator: str | None = Field(
        default=None,
        description="Heading or line a human re-reads to re-derive the fact.",
    )
    sql_case_id: str | None = Field(
        default=None,
        description="Row key in evals/prod_ask/oracle-results.jsonl.",
    )

    @model_validator(mode="after")
    def _require_pointer(self) -> OracleRef:
        if self.kind == "corpus" and not self.corpus_file:
            raise ValueError("corpus oracle needs corpus_file")
        if self.kind == "sql" and not self.sql_case_id:
            raise ValueError("sql oracle needs sql_case_id")
        return self


class LiteralFact(BaseModel):
    """One checkable fact. Numbers compare numerically, text compares as a phrase."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    kind: Literal["text", "number"]
    text: str | None = None
    number: float | None = None
    aliases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_value(self) -> LiteralFact:
        if self.kind == "text" and not self.text:
            raise ValueError("text fact needs text")
        if self.kind == "number" and self.number is None:
            raise ValueError("number fact needs number")
        return self


class AccuracySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    oracle: OracleRef
    required_facts: list[LiteralFact] = Field(default_factory=list)
    forbidden_facts: list[LiteralFact] = Field(default_factory=list)
    judge_claim: str | None = Field(
        default=None,
        description="Claim a judge must verify when the answer is a synthesis, not a literal.",
    )


class CitationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expect_parsed: bool
    supporting_source_files: list[str] = Field(
        default_factory=list,
        description="Basenames of documents that genuinely contain the claim.",
    )
    min_cited: int = 0
    max_cited: int | None = None


class StructureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["table", "list", "prose"]
    min_body_rows: int = 0
    min_columns: int = 0
    min_items: int = 0


class RichTextSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    structure: StructureSpec
    currency_tokens: list[str] = Field(
        default_factory=list,
        description="Closed allowlist of currency labels permitted beside a money amount.",
    )
    forbidden_currency_symbols: list[str] = Field(default_factory=list)
    oracle_amounts: list[float] = Field(
        default_factory=list,
        description="Money values from the oracle that must render with a currency token.",
    )


class FollowUpSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expect_any: bool
    question_resources: list[str] = Field(
        default_factory=list,
        description="Manifest resource names the question itself names.",
    )
    max_suggestions: int = 3
    expected_varies_by_permission: bool = Field(
        default=False,
        description="Whether the paired case must produce a different suggestion set.",
    )


class HelpfulnessSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_need: str = Field(min_length=1)
    must_enable: list[str] = Field(default_factory=list)
    min_score: int = Field(default=3, ge=1, le=5)


class StubDoc(BaseModel):
    """One fixture chunk the stub retriever may return, tagged with its tier."""

    model_config = ConfigDict(extra="forbid")

    source_file: str = Field(min_length=1)
    access_tier: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    content: str = Field(min_length=1)
    section: str | None = None


class StubSpec(BaseModel):
    """Scripted generation output plus the tier-tagged corpus the stub serves.

    The scripted answer is a fixture of what a model said. Stub mode therefore
    proves what the route DOES with that text, never that the text was correct.
    """

    model_config = ConfigDict(extra="forbid")

    docs: list[StubDoc] = Field(default_factory=list)
    model_answer: str = Field(min_length=1)


class ProdAskCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    review_status: Literal["draft", "approved"] = "draft"
    dimensions: list[Dimension] = Field(min_length=1)
    question: str = Field(min_length=1)
    principal: PrincipalSpec
    route: QueryType
    expected_access_tiers: list[str] = Field(default_factory=list)
    modes: list[RunMode] = Field(min_length=1)
    pair_id: str | None = Field(
        default=None,
        description="Twin case asking the same question as a different principal.",
    )
    grounding: str = Field(min_length=1)
    accuracy: AccuracySpec | None = None
    citations: CitationSpec | None = None
    rich_text: RichTextSpec | None = None
    follow_ups: FollowUpSpec | None = None
    helpfulness: HelpfulnessSpec | None = None
    stub: StubSpec | None = None

    @model_validator(mode="after")
    def _spec_present_for_every_dimension(self) -> ProdAskCase:
        missing = [dimension for dimension in self.dimensions if getattr(self, dimension) is None]
        if missing:
            raise ValueError(f"{self.id}: dimensions without a spec: {sorted(missing)}")
        if "stub" in self.modes and self.stub is None:
            raise ValueError(f"{self.id}: stub mode needs a stub spec")
        return self


class AskObservation(BaseModel):
    """One scored ask: the answer plus the run facts the checks cannot infer."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    mode: RunMode
    transport: Transport
    answer: Answer
    retrieved_source_ids: list[str] | None = Field(
        default=None,
        description="Ids the retriever returned before citation filtering; None when unobservable.",
    )
    granted_access_tiers: list[str] | None = None


class SqlOracleRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    sql_sha256: str
    columns: list[str]
    row_count: int
    rows: list[list[object]]
    scalar: object | None = None


def load_cases(path: str | Path) -> dict[str, ProdAskCase]:
    """Load and validate the case file, keyed by case id."""
    cases: dict[str, ProdAskCase] = {}
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = ProdAskCase.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
        if case.id in cases:
            raise ValueError(f"{path}:{line_number}: duplicate case id {case.id}")
        cases[case.id] = case
    if not cases:
        raise ValueError(f"{path}: no cases")
    return cases


def load_sql_oracle(path: str | Path) -> dict[str, SqlOracleRow]:
    """Load the DB-derived answer key, keyed by oracle case id."""
    target = Path(path)
    if not target.exists():
        return {}
    oracle: dict[str, SqlOracleRow] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = SqlOracleRow.model_validate(json.loads(line))
        oracle[row.case_id] = row
    return oracle
