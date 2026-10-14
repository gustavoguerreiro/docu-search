import logging
import sys
from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import SecretStr
from redis import Redis
from rq import Queue, SimpleWorker, Worker

from docusearch.core.settings import Settings
from docusearch.db.mongo import MongoDatabase
from docusearch.jobs import PROCESS_DOCUMENT_JOB, JobQueueError
from docusearch.jobs.redis_queue import FAILURE_TTL_SECONDS, RqJobQueue, create_redis
from docusearch.main import create_app
from docusearch.worker.__main__ import healthcheck

DOCUMENT_ID = "64b7f0c2a1d4e5f6a7b8c9d0"


@pytest.fixture
def job_queue(redis_client: Redis, queue_name: str) -> RqJobQueue:
    return RqJobQueue.from_client(redis_client, queue_name=queue_name, job_timeout_seconds=90)


@pytest.fixture
def rq_queue(redis_client: Redis, queue_name: str) -> Queue:
    return Queue(queue_name, connection=redis_client)


# --- producing jobs ------------------------------------------------------------------------------


async def test_enqueue_puts_one_small_job_on_the_queue(
    job_queue: RqJobQueue, rq_queue: Queue
) -> None:
    job_id = await job_queue.enqueue_document(DOCUMENT_ID)

    assert rq_queue.count == 1
    job = rq_queue.jobs[0]
    assert job.id == job_id
    assert job.func_name == PROCESS_DOCUMENT_JOB
    assert job.args == (DOCUMENT_ID,)  # an id and nothing else
    assert job.kwargs == {}
    assert job.timeout == 90
    assert job.result_ttl == 0
    assert job.failure_ttl == FAILURE_TTL_SECONDS


async def test_jobs_are_queued_in_order(job_queue: RqJobQueue, rq_queue: Queue) -> None:
    ids = [await job_queue.enqueue_document(f"{i:024x}") for i in range(3)]

    assert [job.id for job in rq_queue.jobs] == ids


async def test_enqueue_to_a_dead_redis_is_a_queue_error(mongo_settings: Settings) -> None:
    dead = create_redis(
        mongo_settings.model_copy(
            update={"redis_url": SecretStr("redis://127.0.0.1:1/0"), "redis_timeout_ms": 200}
        )
    )
    try:
        with pytest.raises(JobQueueError):
            await RqJobQueue.from_client(dead).enqueue_document(DOCUMENT_ID)
    finally:
        dead.close()


# --- readiness through the real app --------------------------------------------------------------


@pytest.fixture
async def api(
    mongo_settings: Settings, mongo_db: MongoDatabase
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(mongo_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_readiness_reports_redis(api: httpx.AsyncClient) -> None:
    response = await api.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["redis"] == "ok"


async def test_the_api_stays_alive_and_reports_unready_when_redis_is_down(
    mongo_settings: Settings, mongo_db: MongoDatabase
) -> None:
    settings = mongo_settings.model_copy(
        update={"redis_url": SecretStr("redis://127.0.0.1:1/0"), "redis_timeout_ms": 300}
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):  # must start without Redis
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            ready = await http.get("/api/v1/health/ready")
            live = await http.get("/api/v1/health/live")

    assert ready.status_code == 503
    assert ready.json()["checks"]["redis"] == "fail"
    assert ready.json()["checks"]["mongodb"] == "ok"  # failures are reported independently
    assert live.status_code == 200


# --- the worker ----------------------------------------------------------------------------------


def test_healthcheck_is_false_without_a_worker_and_true_with_one(
    mongo_settings: Settings, redis_client: Redis
) -> None:
    # The health check looks at the production queue name, so register a worker on that queue.
    from docusearch.jobs import QUEUE_NAME

    assert healthcheck(mongo_settings) == 1

    worker = Worker([Queue(QUEUE_NAME, connection=redis_client)], connection=redis_client)
    worker.register_birth()  # type: ignore[no-untyped-call]
    try:
        assert healthcheck(mongo_settings) == 0
    finally:
        worker.register_death()  # type: ignore[no-untyped-call]

    assert healthcheck(mongo_settings) == 1


@pytest.mark.skipif(
    sys.platform == "win32", reason="RQ workers need POSIX signals; runs on Linux and in CI"
)
async def test_a_worker_picks_the_job_up_and_runs_the_job_function(
    job_queue: RqJobQueue, rq_queue: Queue, redis_client: Redis, caplog: pytest.LogCaptureFixture
) -> None:
    await job_queue.enqueue_document(DOCUMENT_ID)

    with caplog.at_level(logging.INFO, logger="docusearch.worker.jobs"):
        processed = SimpleWorker([rq_queue], connection=redis_client).work(burst=True)

    # RQ forgets a worker's counters when it exits, so the evidence is the job's own record, an
    # empty queue and an empty failed registry.
    assert processed is True
    assert rq_queue.count == 0
    assert rq_queue.failed_job_registry.count == 0
    received = [r for r in caplog.records if r.getMessage() == "document job received"]
    assert [r.__dict__["document_id"] for r in received] == [DOCUMENT_ID]
