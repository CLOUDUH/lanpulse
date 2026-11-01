import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.config import DEFAULT_CONFIG
from app.snmp_feed import SnmpFeed


class SnmpFeedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 1000.0
        self.config = deepcopy(DEFAULT_CONFIG)
        self.config["snmp"].update(enabled=True, host="router.local", interface_index=2)
        self.collector = SimpleNamespace(sample=AsyncMock(side_effect=self.read),
                                         reset=AsyncMock(), close=AsyncMock())
        self.feed = SnmpFeed(self.collector, lambda: self.config)
        self.clock = patch("app.snmp_feed.time", SimpleNamespace(
            monotonic=lambda: self.now, time=lambda: self.now,
        ))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    async def read(self, cfg, **kwargs):
        return {"status": "up", "enabled": True, "sampled_at": self.now,
                "download_bps": 12000000, "upload_bps": 2000000}

    async def test_all_clients_share_history_without_starting_new_polls(self):
        await self.feed.poll()
        packets = [self.feed.packet() for _ in range(8)]
        self.collector.sample.assert_awaited_once()
        self.assertTrue(all(packet == packets[0] for packet in packets))
        self.assertEqual(len(packets[0]["wan_samples"]), 1)

    async def test_reconnect_gets_history_and_existing_client_only_gets_new_samples(self):
        await self.feed.poll()
        first = self.feed.packet()["wan"]["sequence"]
        self.now += 1
        await self.feed.poll()
        self.assertEqual(len(self.feed.packet()["wan_samples"]), 2)
        update = self.feed.packet(after=first)
        self.assertEqual(len(update["wan_samples"]), 1)
        self.assertEqual(update["wan_samples"][0]["sampled_at"], self.now)

    async def test_history_is_limited_by_real_time(self):
        for _ in range(130):
            await self.feed.poll()
            self.now += 1
        packet = self.feed.packet()
        self.assertEqual(len(packet["wan_samples"]), 120)
        self.assertTrue(all(sample["sampled_at"] >= self.now - 120 for sample in packet["wan_samples"]))

    async def test_configuration_change_resets_the_source_and_old_history(self):
        await self.feed.poll()
        original = self.feed.generation
        self.config["snmp"]["interface_index"] = 3
        self.now += 1
        await self.feed.poll()
        self.assertEqual(self.feed.generation, original + 1)
        self.assertEqual(len(self.feed.samples), 1)
        self.assertEqual(self.collector.reset.await_count, 2)

    async def test_background_cadence_includes_request_duration(self):
        delays = []

        async def read(cfg, **kwargs):
            self.now += 0.2
            return await self.read(cfg)

        async def sleep(delay):
            delays.append(delay)
            self.now += delay
            if len(delays) == 3:
                raise asyncio.CancelledError

        self.collector.sample = AsyncMock(side_effect=read)
        with patch("app.snmp_feed.asyncio", SimpleNamespace(sleep=sleep)):
            with self.assertRaises(asyncio.CancelledError):
                await self.feed.run()
        self.assertEqual(len(delays), 3)
        for delay in delays:
            self.assertAlmostEqual(delay, 0.8)

    async def test_shutdown_cancels_pending_request_and_closes_the_engine(self):
        started = asyncio.Event()

        async def read(cfg, **kwargs):
            started.set()
            await asyncio.Event().wait()

        self.collector.sample = AsyncMock(side_effect=read)
        self.feed.start()
        await asyncio.wait_for(started.wait(), 1)
        await self.feed.close()
        self.collector.close.assert_awaited_once()
        self.assertIsNone(self.feed._task)
