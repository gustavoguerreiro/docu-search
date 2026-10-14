"""Worker process entry point.

python -m docusearch.worker               run the worker
python -m docusearch.worker --healthcheck exit 0 if a worker is registered for the queue
"""

import logging
import os
import socket
import sys

from rq import Queue, Worker

from docusearch.core.logging_setup import configure_logging
from docusearch.core.settings import Settings, get_settings
from docusearch.jobs import QUEUE_NAME
from docusearch.jobs.redis_queue import create_redis

logger = logging.getLogger(__name__)


def healthcheck(settings: Settings) -> int:
    connection = create_redis(settings)
    try:
        queue = Queue(QUEUE_NAME, connection=connection)
        return 0 if Worker.count(queue=queue) > 0 else 1
    except Exception:
        return 1
    finally:
        connection.close()


def run(settings: Settings) -> int:
    configure_logging(settings.log_level)
    connection = create_redis(settings)
    queue = Queue(QUEUE_NAME, connection=connection)
    name = f"docusearch-{socket.gethostname()}-{os.getpid()}"
    logger.info("worker starting", extra={"queue": QUEUE_NAME, "worker": name})

    worker = Worker([queue], connection=connection, name=name)
    # RQ runs each job in a forked child and handles SIGTERM as a warm shutdown: it finishes the
    # current job and exits. Anything that must outlive a fork (database clients) is created inside
    # the job, never here.
    worker.work(with_scheduler=False, logging_level=settings.log_level)
    logger.info("worker stopped")
    return 0


def main(argv: list[str]) -> int:
    settings = get_settings()
    if "--healthcheck" in argv:
        return healthcheck(settings)
    return run(settings)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
