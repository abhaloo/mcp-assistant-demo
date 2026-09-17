"""Business orchestration — routes stay thin; services own dispatch logic."""

from app.core.errors import ServiceUnavailableError

__all__ = ["ServiceUnavailableError"]
