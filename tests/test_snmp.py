import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.snmp import SnmpCollector


class SnmpCollectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 100.0
        self.clock = patch("app.snmp.time", SimpleNamespace(
            monotonic=lambda: self.now, time=lambda: self.now + 1700000000,
        ))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.config = {
            "enabled": True, "host": "router.local", "port": 161,
            "interface_index": 2, "community": "readonly", "poll_seconds": 2,
            "download_oid": "in.{ifIndex}", "upload_oid": "out.{ifIndex}",
        }
        self.collector = SnmpCollector()

    async def test_concurrent_clients_share_one_sample_and_baseline(self):
        pairs = iter([(1000, 500), (2000, 1000)])

        async def read_pair(_):
            await asyncio.sleep(0)
            return next(pairs)

        self.collector._get_pair = AsyncMock(side_effect=read_pair)
        first = await asyncio.gather(*(self.collector.sample(self.config) for _ in range(8)))
        self.assertEqual(self.collector._get_pair.await_count, 1)
        self.assertTrue(all(result["status"] == "warming" for result in first))
        self.now += 2
        samples = await asyncio.gather(*(self.collector.sample(self.config) for _ in range(8)))
        self.assertEqual(self.collector._get_pair.await_count, 2)
        self.assertTrue(all(result == samples[0] for result in samples))
        self.assertEqual(samples[0]["download_bps"], 4000)
        self.assertEqual(samples[0]["upload_bps"], 2000)

    async def test_extra_requests_do_not_create_zero_samples(self):
        self.collector._get_pair = AsyncMock(side_effect=[(1000, 500), (3000, 1500)])
        await self.collector.sample(self.config)
        self.now += 2
        first = await self.collector.sample(self.config)
        self.now += 0.5
        cached = await self.collector.sample(self.config)
        self.assertEqual(first, cached)
        self.assertEqual(cached["download_bps"], 8000)
        self.assertEqual(self.collector._get_pair.await_count, 2)

    async def test_warming_and_disabled_are_missing_not_zero(self):
        self.collector._get_pair = AsyncMock(return_value=(1000, 500))
        warming = await self.collector.sample(self.config)
        disabled = await self.collector.sample({"enabled": False})
        self.assertIsNone(warming["download_bps"])
        self.assertIsNone(disabled["download_bps"])
        self.assertIn("sampled_at", warming)

    async def test_counter_reset_rewarms_instead_of_spiking(self):
        self.collector._get_pair = AsyncMock(side_effect=[(10000, 5000), (10, 5), (1010, 505)])
        await self.collector.sample(self.config)
        self.now += 2
        reset = await self.collector.sample(self.config)
        self.assertEqual(reset["status"], "warming")
        self.assertIsNone(reset["download_bps"])
        self.now += 2
        recovered = await self.collector.sample(self.config)
        self.assertEqual(recovered["download_bps"], 4000)

    async def test_timeout_is_cached_as_a_gap_and_preserves_baseline(self):
        self.collector._get_pair = AsyncMock(side_effect=[(1000, 500), RuntimeError("timeout"), (3000, 1500)])
        await self.collector.sample(self.config)
        self.now += 2
        error = await self.collector.sample(self.config)
        self.assertEqual(error["status"], "error")
        self.assertIsNone(error["download_bps"])
        self.assertEqual(await self.collector.sample(self.config), error)
        self.assertEqual(self.collector._get_pair.await_count, 2)
        self.now += 2
        recovered = await self.collector.sample(self.config)
        self.assertEqual(recovered["download_bps"], 4000)

    async def test_oid_change_uses_a_new_baseline(self):
        self.collector._get_pair = AsyncMock(side_effect=[(1000, 500), (2000, 1000)])
        await self.collector.sample(self.config)
        changed = await self.collector.sample(dict(self.config, download_oid="other.{ifIndex}"))
        self.assertEqual(changed["status"], "warming")
        self.assertEqual(self.collector._get_pair.await_count, 2)

    async def test_real_zero_traffic_is_still_a_valid_sample(self):
        self.collector._get_pair = AsyncMock(return_value=(1000, 500))
        await self.collector.sample(self.config)
        self.now += 2
        result = await self.collector.sample(self.config)
        self.assertEqual(result["status"], "up")
        self.assertEqual(result["download_bps"], 0)

    async def test_elapsed_time_is_measured_at_counter_receipt(self):
        async def read_pair(_):
            self.now += 0.5
            return (1000, 500)

        self.collector._get_pair = AsyncMock(side_effect=read_pair)
        await self.collector.sample(self.config)
        self.now += 2
        self.collector._get_pair = AsyncMock(return_value=(3000, 1500))
        result = await self.collector.sample(self.config)
        self.assertEqual(result["download_bps"], 8000)
