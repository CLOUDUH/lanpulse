import asyncio
from collections import deque
from contextlib import suppress
import time
from typing import Any, Callable

from .snmp import SnmpCollector, safe_sample


class SnmpFeed:
    """One polling task and a shared history, independent of slow dashboard sources."""

    def __init__(self, collector: SnmpCollector, load_config: Callable):
        self.collector = collector
        self.load_config = load_config
        self.latest: dict[str, Any] = {"status": "warming", "enabled": True}
        self.samples: deque = deque(maxlen=240)
        self.generation = 0
        self.sequence = 0
        self._key: tuple | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(), name="snmp-poller")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.collector.close()

    async def poll(self) -> float:
        cfg = self.load_config()["snmp"]
        key = tuple(cfg.get(field) for field in (
            "enabled", "host", "port", "interface_index", "community",
            "download_oid", "upload_oid", "poll_seconds", "timeout_seconds", "counter_window_seconds",
        ))
        if key != self._key:
            self._key = key
            self.samples.clear()
            self.generation += 1
            await self.collector.reset()
        # This is the sole producer: never skip a scheduled tick because an OS
        # timer woke a fraction of a millisecond before the cache TTL elapsed.
        result = await safe_sample(self.collector, cfg, force=True)
        stamp = result.get("sampled_at")
        if stamp != self.latest.get("sampled_at") or result.get("status") != self.latest.get("status"):
            self.sequence += 1
            self.latest = dict(result, sequence=self.sequence)
            if result.get("status") != "disabled":
                self.samples.append(self.latest)
        return max(1, min(60, float(cfg.get("poll_seconds", 1))))

    async def run(self) -> None:
        while True:
            started = time.monotonic()
            try:
                interval = await self.poll()
            except Exception as exc:
                self.sequence += 1
                self.latest = {"enabled": True, "status": "error", "error": str(exc),
                               "download_bps": None, "upload_bps": None,
                               "sampled_at": time.time(), "sequence": self.sequence}
                self.samples.append(self.latest)
                interval = 2
            # The request duration belongs to the interval, rather than adding delay.
            await asyncio.sleep(max(0.01, interval - (time.monotonic() - started)))

    def packet(self, after: int = 0) -> dict[str, Any]:
        now = time.time()
        return {
            "server_time": now,
            "wan": dict(self.latest),
            "wan_generation": self.generation,
            "wan_samples": [dict(sample) for sample in self.samples
                            if sample["sequence"] > after and sample["sampled_at"] >= now - 120],
        }
