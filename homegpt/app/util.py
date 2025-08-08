import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta

class RateLimiter:
    def __init__(self, max_per_hour: int):
        self.max_per_hour = max_per_hour
        self.events = deque()

    def allow(self) -> bool:
        now = datetime.utcnow()
        while self.events and (now - self.events[0]) > timedelta(hours=1):
            self.events.popleft()
        if len(self.events) < self.max_per_hour:
            self.events.append(now)
            return True
        return False

def setup_logging(level: str = "INFO"):
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format='[%(asctime)s] %(levelname)s %(name)s: %(message)s')

async def next_time_of_day(hhmm: str) -> float:
    # seconds until the next hh:mm in local container time
    now = datetime.now()
    hour, minute = map(int, hhmm.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()