from __future__ import annotations

import json
import logging
from typing import Any

from .config import settings

logger = logging.getLogger(__name__)

# True when a Redis URL is configured and the redis package is importable.
IS_REDIS = False

try:
    if settings.redis_url:
        import redis  # noqa: F401
        IS_REDIS = True
except ImportError:
    logger.warning("REDIS_URL is set but the redis package is not installed; SSE will poll the DB.")


def job_channel(job_id: str) -> str:
    return f"job:{job_id}"


def publish_job_state(job_id: str, payload: dict[str, Any]) -> None:
    """Publish job state to the job's Redis channel. No-op when Redis is off."""
    if not IS_REDIS:
        return
    try:
        import redis as _redis
        client = _redis.Redis.from_url(settings.redis_url)
        client.publish(job_channel(job_id), json.dumps(payload))
        client.close()
    except Exception:
        logger.exception("failed to publish job state for %s", job_id)
