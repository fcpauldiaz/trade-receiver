import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

_loop_state: WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    tuple[defaultdict[str, asyncio.Lock], asyncio.Lock],
] = WeakKeyDictionary()


def _locks_for_loop() -> tuple[defaultdict[str, asyncio.Lock], asyncio.Lock]:
    loop = asyncio.get_running_loop()
    state = _loop_state.get(loop)
    if state is None:
        state = (defaultdict(asyncio.Lock), asyncio.Lock())
        _loop_state[loop] = state
    return state


@asynccontextmanager
async def ingest_processing_slot(user_id: str):
    """Process one ingest at a time per user; serialize Turso/libsql writes globally."""
    user_locks, db_write_lock = _locks_for_loop()
    async with user_locks[user_id]:
        async with db_write_lock:
            yield
