"""Presidio analyzer and anonymizer engine setup (shared across guardrail paths)."""

import threading
from dataclasses import dataclass
from functools import lru_cache

from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import PhoneRecognizer
from presidio_anonymizer import AnonymizerEngine

from app.config import settings
from app.guardrails.context_filter import filter_id_context


@lru_cache(maxsize=1)
def get_analyzer() -> AnalyzerEngine:
    """
    Build and cache the Presidio AnalyzerEngine.

    Registers a Tanzania-region PhoneRecognizer so analyze() detects
    +255/0XXX phone formats found in the customer DB.

    Pins the spaCy model to en_core_web_lg, which the Dockerfile bakes into
    the image at BUILD time. The explicit nlp_engine is load-bearing either
    way: without it Presidio tries to *download* the model at runtime, which
    fails in the hardened non-root container (root-owned /opt/venv →
    Permission denied → AnalyzerEngine init raises → 500 on every redacted
    request). Baking lg at build time (not runtime) is what makes the large
    model usable here — at the cost of ~400MB image size / longer cold start.
    """
    phone_recognizer = PhoneRecognizer(supported_regions=[settings.phone_region])

    nlp_engine = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}],
        }
    ).create_engine()

    analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
    analyzer.registry.add_recognizer(phone_recognizer)

    return analyzer


@lru_cache(maxsize=1)
def get_anonymizer() -> AnonymizerEngine:
    """Build and cache the Presidio AnonymizerEngine. Defaults are fine for V1."""
    return AnonymizerEngine()


# Presidio's AnalyzerEngine wraps ONE shared spaCy pipeline whose Vocab/StringStore
# mutates on every parse — it is NOT safe for concurrent calls from multiple threads
# (the eval harness runs cases on a thread pool; FastAPI also serves sync endpoints on a
# threadpool, so this guards a latent prod race too). Detection is milliseconds, so
# serializing it does not bottleneck the slow LLM calls that dominate wall-clock.
_ANALYZE_LOCK = threading.Lock()


@dataclass(frozen=True)
class PiiDetection:
    findings: list[RecognizerResult]
    raw_count: int
    id_context_suppressed: int


def detect_pii(
    text: str, *, id_context_filter: bool, entities: list[str] | None = None
) -> PiiDetection:
    """THE detection policy. Both PII pipelines call this — never analyzer.analyze
    directly — so entities/threshold/language changes land everywhere at once.
    Divergence between pipelines is the explicit id_context_filter flag (ADR 0020).

    `entities` overrides the detected entity set for callers whose threat surface
    differs from doc redaction — e.g. the LangSmith export boundary scrubs user
    questions against a broad allow-list, not just settings.redaction_entities.
    Defaults to settings.redaction_entities so existing callers are unchanged."""
    with _ANALYZE_LOCK:
        raw = get_analyzer().analyze(
            text=text,
            language="en",
            entities=entities if entities is not None else settings.redaction_entities,
            score_threshold=settings.redaction_confidence_threshold,
        )
    findings = filter_id_context(text, raw) if id_context_filter else list(raw)
    return PiiDetection(
        findings=findings,
        raw_count=len(raw),
        id_context_suppressed=len(raw) - len(findings),
    )
