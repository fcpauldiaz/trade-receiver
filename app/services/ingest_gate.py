import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

_loop_state: WeakKeyDictionary[asyncio.AbstractEventLoop, defaultdict[str, asyncio.Lock]] = (
    WeakKeyDictionary()
)


def _locks_for_loop() -> defaultdict[str, asyncio.Lock]:
    loop = asyncio.get_running_loop()
    state = _loop_state.get(loop)
    if state is None:
        state = defaultdict(asyncio.Lock)
        _loop_state[loop] = state
    return state


@asynccontextmanager
async def ingest_processing_slot(user_id: str):
    """Process one ingest at a time per user."""
    user_locks = _locks_for_loop()
    async with user_locks[user_id]:
        yield
