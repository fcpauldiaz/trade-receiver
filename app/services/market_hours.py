"""US equity regular trading hours (RTH) checks."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)
RTH_SKIP_REASON = "outside regular trading hours (9:30 AM – 4:00 PM ET, Mon–Fri)"


def _to_et(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=ET)
    return now.astimezone(ET)


def is_rth(now: datetime | None = None) -> bool:
    """Return True when *now* falls within NYSE regular trading hours."""
    current = _to_et(now or datetime.now(ET))
    if current.weekday() >= 5:
        return False
    clock = current.time()
    return RTH_OPEN <= clock < RTH_CLOSE
