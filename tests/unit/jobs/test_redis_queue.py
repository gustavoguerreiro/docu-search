import logging
import threading
from typing import Any

import pytest
import redis
from pydantic import SecretStr

from docusearch.core.settings import Settings
from docusearch.jobs import PROCESS_DOCUMENT_JOB, QUEUE_NAME, JobQueueError
from docusearch.jobs.redis_queue import (
    FAILURE_TTL_SECONDS,
    RqJobQueue,
    create_redis,
    ping,
)

SECRET_URL = "redis://:s3cr3t-pass@queue.internal:6379/0"


class FakeJob:
    id = "job-123"


class FakeQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.thread: str | None = None
        self.error: Exception | None = None

    def enqueue(self, f: str, *args: Any, **kwargs: Any) -> FakeJob:
        self.thread = threading.current_thread().name
        if self.error is not None:
            raise self.error
        self.calls.append(((f, *args), kwargs))
        return FakeJob()


class FakeRedis:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def ping(self) -> bool:
        if self.error is not None:
            raise self.error
        return True


# --- settings and client -------------------------------------------------------------------------


def test_redis_defaults_and_secrecy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("REDIS_URL", "REDIS_TIMEOUT_MS"):
        monkeypatch.delenv(name, raising=False)
    default = Settings(_env_file=None)
    assert default.redis_url.get_secret_value() == "redis://localhost:6379/0"
    assert default.redis_timeout_ms == 2000

    configured = Settings(_env_file=None, redis_url=SecretStr(SECRET_URL))
    assert "s3cr3t-pass" not in repr(configured)
    assert "s3cr3t-pass" not in configured.model_dump_json()


@pytest.mark.parametrize("value", [0, 99, 60_001])
def test_redis_timeout_is_bounded(value: int) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, redis_timeout_ms=value)


def test_the_client_has_bounded_timeouts_and_does_not_connect_on_creation() -> None:
    settings = Settings(
        _env_file=None, redis_url=SecretStr("redis://127.0.0.1:1/0"), redis_timeout_ms=300
    )

    client = create_redis(settings)  # must not raise although nothing listens on port 1
    kwargs = client.connection_pool.connection_kwargs

    assert kwargs["socket_connect_timeout"] == 0.3
    assert kwargs["socket_timeout"] == 0.3
    client.close()


# --- enqueue -------------------------------------------------------------------------------------


async def test_a_document_job_carries_only_its_id_and_explicit_limits() -> None:
    fake = FakeQueue()

    job_id = await RqJobQueue(fake, job_timeout_seconds=120).enqueue_document("abc123")

    assert job_id == "job-123"
    ((args, kwargs),) = fake.calls
    assert args == (PROCESS_DOCUMENT_JOB, "abc123")
    assert kwargs == {
        "job_timeout": 120,
        "result_ttl": 0,
        "failure_ttl": FAILURE_TTL_SECONDS,
    }


async def test_enqueue_happens_off_the_event_loop() -> None:
    fake = FakeQueue()

    await RqJobQueue(fake).enqueue_document("abc123")

    assert fake.thread is not None
    assert fake.thread != threading.current_thread().name


async def test_a_redis_failure_becomes_a_queue_error_without_the_original_message() -> None:
    fake = FakeQueue()
    fake.error = redis.ConnectionError(f"cannot reach {SECRET_URL}")

    with pytest.raises(JobQueueError) as caught:
        await RqJobQueue(fake).enqueue_document("abc123")

    assert "s3cr3t-pass" not in str(caught.value)


async def test_unexpected_errors_are_not_swallowed() -> None:
    fake = FakeQueue()
    fake.error = RuntimeError("programming error")

    with pytest.raises(RuntimeError):
        await RqJobQueue(fake).enqueue_document("abc123")


def test_the_queue_name_is_stable() -> None:
    # Producers and the worker must agree on it; changing it silently would strand queued jobs.
    assert QUEUE_NAME == "documents"
    assert PROCESS_DOCUMENT_JOB == "docusearch.worker.jobs.process_document"


# --- readiness -----------------------------------------------------------------------------------


async def test_ping_reports_health() -> None:
    assert await ping(FakeRedis()) is True  # type: ignore[arg-type]


async def test_ping_failure_is_false_and_logs_only_the_error_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broken = FakeRedis(redis.ConnectionError(f"Error connecting to {SECRET_URL}"))

    with caplog.at_level(logging.WARNING):
        assert await ping(broken) is False  # type: ignore[arg-type]

    assert caplog.records[0].__dict__["error_type"] == "ConnectionError"
    assert "s3cr3t-pass" not in caplog.text


async def test_ping_against_a_dead_server_fails_fast() -> None:
    settings = Settings(
        _env_file=None, redis_url=SecretStr("redis://127.0.0.1:1/0"), redis_timeout_ms=200
    )
    client = create_redis(settings)
    try:
        assert await ping(client) is False
    finally:
        client.close()
