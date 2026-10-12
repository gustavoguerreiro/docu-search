import logging
from collections.abc import Iterator

import pytest

_MANAGED_LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """`configure_logging` mutates global logger state; undo it after every test."""
    saved = [
        (logger, logger.handlers[:], logger.level, logger.propagate)
        for logger in map(logging.getLogger, _MANAGED_LOGGERS)
    ]
    yield
    for logger, handlers, level, propagate in saved:
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate
