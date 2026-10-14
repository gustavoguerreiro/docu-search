"""Job functions executed by the worker. RQ imports them by dotted path."""

import logging

logger = logging.getLogger(__name__)


def process_document(document_id: str) -> None:
    """Process one document.

    Placeholder: it only records that the job arrived. Extraction, chunking and the state
    transitions are added in the following steps of Milestone 5.
    """
    logger.info("document job received", extra={"document_id": document_id})
