from datetime import datetime, timezone

from app.models.types import UnixTimestampMs
from tests.db_helpers import BETTER_AUTH_CREATED_AT_MS


def test_unix_timestamp_ms_loads_better_auth_integer():
    loaded = UnixTimestampMs().process_result_value(BETTER_AUTH_CREATED_AT_MS, None)
    assert isinstance(loaded, datetime)
    assert loaded == datetime.fromtimestamp(
        BETTER_AUTH_CREATED_AT_MS / 1000.0, tz=timezone.utc
    ).replace(tzinfo=None)


def test_unix_timestamp_ms_loads_iso_datetime_string():
    loaded = UnixTimestampMs().process_result_value("2026-08-15 21:29:02", None)
    assert loaded == datetime(2026, 8, 15, 21, 29, 2)


def test_unix_timestamp_ms_persists_datetime_as_milliseconds():
    value = datetime(2026, 8, 15, 21, 29, 2, tzinfo=timezone.utc)
    stored = UnixTimestampMs().process_bind_param(value, None)
    assert stored == int(value.timestamp() * 1000)
