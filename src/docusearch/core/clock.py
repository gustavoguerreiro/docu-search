"""Time helpers. All timestamps in DocuSearch are timezone-aware UTC."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Current time as an aware UTC datetime, truncated to milliseconds.

    MongoDB stores datetimes with millisecond precision. Truncating here means a value kept in
    memory compares equal to the same value after a database round trip.
    """
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)
