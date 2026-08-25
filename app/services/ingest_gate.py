import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager

_user_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_db_write_lock = asyncio.Lock()


@asynccontextmanager
async def ingest_processing_slot(user_id: str):
    """Process one ingest at a time per user; serialize Turso/libsql writes globally."""
    async with _user_locks[user_id]:
        async with _db_write_lock:
            yield
