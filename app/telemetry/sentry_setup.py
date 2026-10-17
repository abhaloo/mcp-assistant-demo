"""Optional Sentry init — no-op when SENTRY_DSN is unset."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def init_sentry() -> None:
    from app.config import settings

    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed")
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.telemetry.environment,
        integrations=[
            FastApiIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        traces_sample_rate=0.0,
        send_default_pii=False,
    )
    logger.info("Sentry initialized for environment=%s", settings.telemetry.environment)
