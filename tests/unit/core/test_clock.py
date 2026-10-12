from datetime import UTC, datetime, timedelta

from docusearch.core.clock import utc_now


def test_utc_now_is_timezone_aware_utc() -> None:
    now = utc_now()

    assert now.tzinfo is UTC
    assert now.utcoffset() == timedelta(0)


def test_utc_now_is_truncated_to_milliseconds() -> None:
    values = [utc_now() for _ in range(50)]

    assert all(value.microsecond % 1000 == 0 for value in values)


def test_utc_now_tracks_the_real_clock() -> None:
    before = datetime.now(UTC)
    now = utc_now()
    after = datetime.now(UTC)

    # Truncation can only move the value back by less than one millisecond.
    assert before - timedelta(milliseconds=1) <= now <= after


def test_utc_now_is_monotonic_enough_for_ordering() -> None:
    first = utc_now()
    second = utc_now()

    assert first <= second
