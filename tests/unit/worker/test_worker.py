import logging

import pytest
from pydantic import SecretStr

from docusearch.core.settings import Settings
from docusearch.worker.__main__ import healthcheck, main
from docusearch.worker.jobs import process_document


def dead_redis_settings() -> Settings:
    return Settings(
        _env_file=None, redis_url=SecretStr("redis://127.0.0.1:1/0"), redis_timeout_ms=200
    )


def test_healthcheck_fails_when_the_queue_is_unreachable() -> None:
    assert healthcheck(dead_redis_settings()) == 1


def test_main_dispatches_the_healthcheck_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("docusearch.worker.__main__.get_settings", dead_redis_settings)

    assert main(["--healthcheck"]) == 1


def test_the_placeholder_job_only_records_the_id(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        process_document("64b7f0c2a1d4e5f6a7b8c9d0")  # returns nothing and changes nothing

    assert caplog.records[0].__dict__["document_id"] == "64b7f0c2a1d4e5f6a7b8c9d0"
