import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from docusearch.core.logging_setup import REDACTED, configure_logging


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    buffer = io.StringIO()
    try:
        yield buffer
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def emit(stream: io.StringIO, **extra: Any) -> dict[str, Any]:
    configure_logging("INFO", stream=stream)
    logging.getLogger("docusearch.test").info("hello %s", "world", extra=extra)
    return json.loads(stream.getvalue())  # type: ignore[no-any-return]


def test_emits_one_json_object_per_line(stream: io.StringIO) -> None:
    configure_logging("INFO", stream=stream)
    log = logging.getLogger("docusearch.test")
    log.info("first")
    log.warning("second")

    lines = stream.getvalue().splitlines()

    assert [json.loads(line)["message"] for line in lines] == ["first", "second"]


def test_core_fields(stream: io.StringIO) -> None:
    entry = emit(stream)

    assert entry["message"] == "hello world"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "docusearch.test"
    assert entry["timestamp"].endswith("+00:00")  # UTC-aware


def test_extra_fields_are_included(stream: io.StringIO) -> None:
    entry = emit(stream, document_count=3)

    assert entry["document_count"] == 3


def test_extra_cannot_overwrite_core_fields(stream: io.StringIO) -> None:
    entry = emit(stream, level="FAKE", timestamp="yesterday")

    assert entry["level"] == "INFO"
    assert entry["timestamp"] != "yesterday"


def test_sensitive_extra_keys_are_redacted(stream: io.StringIO) -> None:
    entry = emit(
        stream,
        password="hunter2",  # noqa: S106
        refresh_token="abc",  # noqa: S106
        headers={"Authorization": "Bearer xyz", "accept": "json"},
        attempts=[{"api_key": "k"}],
    )

    assert entry["password"] == REDACTED
    assert entry["refresh_token"] == REDACTED
    assert entry["headers"] == {"Authorization": REDACTED, "accept": "json"}
    assert entry["attempts"] == [{"api_key": REDACTED}]
    assert "hunter2" not in stream.getvalue()
    assert "Bearer xyz" not in stream.getvalue()


def test_exception_is_serialized(stream: io.StringIO) -> None:
    configure_logging("INFO", stream=stream)
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("docusearch.test").exception("failed")

    entry = json.loads(stream.getvalue())

    assert entry["level"] == "ERROR"
    assert "ValueError: boom" in entry["exception"]


def test_non_serializable_extra_does_not_break_logging(stream: io.StringIO) -> None:
    entry = emit(stream, thing=object())

    assert entry["thing"].startswith("<object object")


def test_level_filters_records(stream: io.StringIO) -> None:
    configure_logging("WARNING", stream=stream)
    log = logging.getLogger("docusearch.test")
    log.info("hidden")
    log.error("shown")

    assert [json.loads(line)["message"] for line in stream.getvalue().splitlines()] == ["shown"]


def test_configure_logging_is_idempotent(stream: io.StringIO) -> None:
    configure_logging("INFO", stream=stream)
    configure_logging("INFO", stream=stream)
    logging.getLogger("docusearch.test").info("once")

    assert len(stream.getvalue().splitlines()) == 1
