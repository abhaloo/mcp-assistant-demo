# syntax=docker/dockerfile:1

# Demo storefront image — same as upstream minus eval-only COPY lines.

FROM python:3.11.9-slim AS builder

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PIP_NO_CACHE_DIR=1
RUN python -m venv "$VIRTUAL_ENV"

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt ./
RUN pip install --requirement requirements.txt \
    && python -m spacy download en_core_web_lg \
    && python -m spacy download en_core_web_sm

FROM python:3.11.9-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

RUN groupadd --system appgroup \
    && useradd --system --no-log-init --gid appgroup --create-home appuser \
    && mkdir -p /app/data/chroma /app/logs \
    && chown -R appuser:appgroup /app

COPY --chown=appuser:appgroup config ./config
COPY --chown=appuser:appgroup app ./app
COPY --chown=appuser:appgroup scripts ./scripts
COPY --chown=appuser:appgroup alembic.ini ./alembic.ini
COPY --chown=appuser:appgroup alembic ./alembic

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
