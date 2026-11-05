"""Client-side rate limiting: a sliding one-minute request window plus a concurrency cap."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

WINDOW_S = 60.0


class RateLimiter:
    def __init__(
        self,
        requests_per_minute: int,
        max_concurrency: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if requests_per_minute < 1 or max_concurrency < 1:
            raise ValueError("requests_per_minute and max_concurrency must be >= 1")
        self._rpm = requests_per_minute
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._window: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._clock = clock
        self._sleep = sleep

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Hold a concurrency slot for the duration of one request, after reserving window capacity."""
        async with self._semaphore:
            await self._reserve()
            yield

    async def _reserve(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._window and now - self._window[0] >= WINDOW_S:
                    self._window.popleft()
                if len(self._window) < self._rpm:
                    self._window.append(now)
                    return
                await self._sleep(self._window[0] + WINDOW_S - now)
