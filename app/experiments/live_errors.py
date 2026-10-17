"""Shared fail-closed errors for live RAG/SQL campaign shells."""

from __future__ import annotations

from app.experiments.core.errors import RagExperimentError


class LiveRagEvalError(RagExperimentError):
    """Live campaign refused before trustworthy evidence can be produced."""


class LiveAskSmokeError(RagExperimentError):
    """Live ask smoke refused before trustworthy evidence can be produced."""


class LiveRouteConformanceError(RagExperimentError):
    """Live route conformance refused before trustworthy evidence can be produced."""


class LiveSqlEvalError(RagExperimentError):
    """Live SQL campaign refused before trustworthy evidence can be produced."""


class LiveToolSmokeError(RagExperimentError):
    """Live tool smoke refused before trustworthy evidence can be produced."""
