import asyncio
import random
import time


class RateLimiter:
    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max_per_minute
        self.timestamps: list[float] = []

    async def wait(self) -> None:
        now = time.time()
        self.timestamps = [stamp for stamp in self.timestamps if now - stamp < 60]

        if len(self.timestamps) >= self.max_per_minute:
            oldest = self.timestamps[0]
            wait_s = 60 - (now - oldest)
            if wait_s > 0:
                jitter = 1 + random.random() * 2
                await asyncio.sleep(wait_s + jitter)
        elif self.timestamps:
            jitter = 0.5 + random.random() * 1.5
            await asyncio.sleep(jitter)

        self.timestamps.append(time.time())
