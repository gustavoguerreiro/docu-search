"""The job queue boundary.

The API hands work to the worker through `JobQueue` and never talks to Redis or RQ directly, so
the upload path can be tested without them and the queue technology stays replaceable. Payloads
carry only identifiers: a job never contains document content or secrets, and the worker reads the
real state from MongoDB, which is the source of truth.
"""

from typing import Protocol

QUEUE_NAME = "documents"
PROCESS_DOCUMENT_JOB = "docusearch.worker.jobs.process_document"


class JobQueueError(Exception):
    """The queue could not accept a job (backend unavailable)."""


class JobQueue(Protocol):
    async def enqueue_document(self, document_id: str) -> str:
        """Ask the worker to process a document. Returns the job id.

        Raises `JobQueueError` if the job could not be queued.
        """
        ...
