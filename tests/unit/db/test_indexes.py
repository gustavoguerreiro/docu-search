import logging
from typing import Any, cast

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from docusearch.db import collections
from docusearch.db.indexes import INDEXES, IndexInitializer
from docusearch.db.mongo import MongoDatabase


class Script:
    """Replaces ensure_indexes/verify_indexes with a scripted sequence of outcomes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.ensure_calls = 0
        self.verify_calls = 0
        self.ensure_error: Exception | None = None
        self.problems: list[str] = []
        monkeypatch.setattr("docusearch.db.indexes.ensure_indexes", self._ensure)
        monkeypatch.setattr("docusearch.db.indexes.verify_indexes", self._verify)

    async def _ensure(self, db: Any) -> None:
        self.ensure_calls += 1
        if self.ensure_error is not None:
            raise self.ensure_error

    async def _verify(self, db: Any) -> list[str]:
        self.verify_calls += 1
        return self.problems


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> Script:
    return Script(monkeypatch)


def initializer() -> IndexInitializer:
    return IndexInitializer(cast(MongoDatabase, object()))


def test_definitions_cover_every_collection_with_unique_index_names() -> None:
    assert set(INDEXES) == set(collections.ALL)
    for models in INDEXES.values():
        names = [model.document["name"] for model in models]
        assert len(names) == len(set(names))
        assert all(name != "_id_" for name in names)


async def test_ensure_creates_verifies_and_then_stops_working(script: Script) -> None:
    sut = initializer()

    assert await sut.ensure() is True
    assert await sut.ensure() is True

    assert sut.done is True
    assert (script.ensure_calls, script.verify_calls) == (1, 1)


async def test_ensure_reports_false_and_retries_while_the_database_is_down(
    script: Script, caplog: pytest.LogCaptureFixture
) -> None:
    sut = initializer()
    script.ensure_error = ServerSelectionTimeoutError("mongodb://user:s3cr3t@db unreachable")

    with caplog.at_level(logging.WARNING):
        assert await sut.ensure() is False
        assert await sut.ensure() is False
    assert sut.done is False
    assert script.ensure_calls == 2
    assert "s3cr3t" not in caplog.text

    script.ensure_error = None  # the database came back
    assert await sut.ensure() is True
    assert sut.done is True


async def test_ensure_is_not_ready_when_indexes_do_not_match(
    script: Script, caplog: pytest.LogCaptureFixture
) -> None:
    sut = initializer()
    script.problems = ["users.email_unique: different uniqueness"]

    with caplog.at_level(logging.ERROR):
        assert await sut.ensure() is False

    assert sut.done is False
    assert caplog.records[0].__dict__["problems"] == ["users.email_unique: different uniqueness"]


async def test_non_driver_errors_are_not_swallowed(script: Script) -> None:
    script.ensure_error = RuntimeError("programming error")

    with pytest.raises(RuntimeError):
        await initializer().ensure()
