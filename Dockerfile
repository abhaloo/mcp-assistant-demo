# syntax=docker/dockerfile:1

# ============================================================================
# Stage 1 — builder
# ----------------------------------------------------------------------------
# Everything expensive and throwaway happens here: compilers, pip, the spaCy
# model download. None of it ships in the final image — only the finished venv
# crosses the stage boundary. Pinned base tag (not bare 3.11-slim) so two
# builds a month apart use the identical interpreter.
# ============================================================================
FROM python:3.11.9-slim AS builder

# An isolated venv is the unit we copy to the runtime stage — self-contained,
# no reliance on system site-packages.
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PIP_NO_CACHE_DIR=1
RUN python -m venv "$VIRTUAL_ENV"

# Build toolchain for any dependency that ships an sdist instead of a wheel.
# Lives only in this stage, so it never bloats the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Dependencies FIRST, source NEVER. This layer is keyed solely on
# requirements.txt — editing app/ code does not invalidate it, so rebuilds skip
# the ~720-package install and the spaCy download entirely (fixes the old
# cache-busting where COPY app preceded pip install).
COPY requirements.txt ./
# en_core_web_lg: guardrails analyzer (query path). en_core_web_sm: unstructured's
# tokenizer (ingest job, .md loading) — it auto-installs at runtime if absent, which
# fails read-only site-packages as the non-root user.
RUN pip install --requirement requirements.txt \
    && python -m spacy download en_core_web_lg \
    && python -m spacy download en_core_web_sm

# ============================================================================
# Stage 2 — runtime
# ----------------------------------------------------------------------------
# Lean final image: the venv + the app source + a non-root user. No pip cache,
# no build-essential, no requirements.txt. Smaller image => faster ACR pull =>
# shorter scale-to-zero cold start.
# ============================================================================
FROM python:3.11.9-slim AS runtime

# PYTHONUNBUFFERED   — logs flush live to the ACA log stream instead of buffering.
# PYTHONDONTWRITEBYTECODE — no .pyc litter written at runtime.
# PYTHONPATH=/app    — makes `app.main` importable without pip-installing the
#                      project (we copy source instead of building a wheel).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# The only artifact from the builder: the fully-populated venv (incl. the
# en_core_web_sm model installed into site-packages).
COPY --from=builder /opt/venv /opt/venv

# Least-privilege user + the two writable paths the app expects at runtime:
# the Chroma persist dir and the redaction audit log dir.
RUN groupadd --system appgroup \
    && useradd --system --no-log-init --gid appgroup --create-home appuser \
    && mkdir -p /app/data/chroma /app/logs \
    && chown -R appuser:appgroup /app

# Catalog + eligibility YAML live at /app/config (production_catalog.py).
# Missing this COPY crashed UAT: FileNotFoundError on production_model_catalog.yaml.
COPY --chown=appuser:appgroup config ./config

# App source LAST — the cheapest, most frequently-changed layer sits on top so
# code edits rebuild only this one.
COPY --chown=appuser:appgroup app ./app

# The ingest job (infra/main.bicep) runs `python scripts/reindex.py` in this
# same image; its gate loads eval gold from tests/eval_dataset.json.
# Alembic lives here so the one-shot migrate service / ACA Job can run
# `python scripts/migrate_query_records.py` without a bind-mount.
COPY --chown=appuser:appgroup scripts ./scripts
COPY --chown=appuser:appgroup alembic.ini ./alembic.ini
COPY --chown=appuser:appgroup alembic ./alembic
COPY --chown=appuser:appgroup tests/eval_dataset.json ./tests/eval_dataset.json

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
