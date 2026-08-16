from datetime import datetime, timezone

from sqlalchemy import Integer, TypeDecorator


class UnixTimestampMs(TypeDecorator):
    """Better Auth / Drizzle store users timestamps as integer milliseconds."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: datetime | int | float | None, dialect) -> int | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
            return int(aware.timestamp() * 1000)
        if isinstance(value, (int, float)):
            return int(value)
        raise TypeError(f"Cannot persist {type(value)!r} as unix timestamp")

    def process_result_value(self, value: object, dialect) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo else value
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).replace(tzinfo=None)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        raise TypeError(f"Cannot load {type(value)!r} as datetime")
