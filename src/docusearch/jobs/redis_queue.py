"""Redis and RQ implementation of the job queue, plus the Redis client helpers."""

import asyncio
import logging
from typing import Any, Protocol

import redis
from rq import Queue

from docusearch.core.settings import Settings
from docusearch.jobs import PROCESS_DOCUMENT_JOB, QUEUE_NAME, JobQueueError

logger = logging.getLogger(__name__)

# How long RQ keeps a failed job around for inspection. Finished jobs are not kept: nothing reads
# their results, the document record in MongoDB is the outcome.
FAILURE_TTL_SECONDS = 7 * 24 * 3600
DEFAULT_JOB_TIMEOUT_SECONDS = 300


def create_redis(settings: Settings) -> redis.Redis:
    """A synchronous client with bounded timeouts. RQ requires undecoded (bytes) responses.

    It does not connect until first use, so the application can start while Redis is down.
    """
    timeout = settings.redis_timeout_ms / 1000
    return redis.Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
        health_check_interval=30,
    )


async def ping(client: redis.Redis) -> bool:
    """Readiness probe. Only the exception type is logged; messages can contain the URL."""
    try:
        await asyncio.to_thread(client.ping)
    except (redis.RedisError, OSError) as exc:
        logger.warning("redis ping failed", extra={"error_type": type(exc).__name__})
        return False
    return True


class SupportsEnqueue(Protocol):
    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> Any: ...


class RqJobQueue:
    def __init__(
        self,
        queue: SupportsEnqueue,
        *,
        job_timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    ) -> None:
        self._queue = queue
        self._job_timeout = job_timeout_seconds

    @classmethod
    def from_client(
        cls,
        client: redis.Redis,
        *,
        queue_name: str = QUEUE_NAME,
        job_timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    ) -> "RqJobQueue":
        return cls(Queue(queue_name, connection=client), job_timeout_seconds=job_timeout_seconds)

    async def enqueue_document(self, document_id: str) -> str:
        try:
            job = await asyncio.to_thread(
                self._queue.enqueue,
                PROCESS_DOCUMENT_JOB,
                document_id,
                job_timeout=self._job_timeout,
                result_ttl=0,
                failure_ttl=FAILURE_TTL_SECONDS,
            )
        except (redis.RedisError, OSError) as exc:
            raise JobQueueError("could not enqueue job") from exc
        return str(job.id)
