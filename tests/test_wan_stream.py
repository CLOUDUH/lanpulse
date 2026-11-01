import asyncio
from copy import deepcopy
import importlib
import json
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.config import DEFAULT_CONFIG


class WanStreamTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {"DATA_DIR": cls.directory.name}):
            cls.main = importlib.import_module("app.main")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    async def test_wan_stream_is_available_while_other_sources_are_blocked(self):
        started = asyncio.Event()

        async def slow_source(cfg):
            started.set()
            await asyncio.Event().wait()

        packet = {"server_time": 1000, "wan_generation": 1,
                  "wan": {"sequence": 1, "status": "up", "download_bps": 12000000},
                  "wan_samples": [{"sampled_at": 1000, "download_bps": 12000000}]}
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
        with patch.object(self.main.store, "load", return_value=deepcopy(DEFAULT_CONFIG)), \
                patch.object(self.main.beszel, "snapshot", AsyncMock(side_effect=slow_source)), \
                patch.object(self.main, "safe_weather", AsyncMock(side_effect=slow_source)), \
                patch.object(self.main, "safe_terminal", AsyncMock(side_effect=slow_source)), \
                patch.object(self.main, "snmp_feed", SimpleNamespace(packet=Mock(return_value=packet))):
            response = await self.main.events(request)
            blocked = asyncio.create_task(anext(response.body_iterator))
            try:
                await asyncio.wait_for(started.wait(), 1)
                wan_response = await self.main.wan_events(request)
                message = await asyncio.wait_for(anext(wan_response.body_iterator), 0.2)
                self.assertEqual(json.loads(message.removeprefix("data: ").strip()), packet)
                self.assertFalse(blocked.done())
                await wan_response.body_iterator.aclose()
            finally:
                blocked.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await blocked

    async def test_general_snapshot_uses_cached_wan_without_shifting_other_fields(self):
        with patch.object(self.main.store, "load", return_value=deepcopy(DEFAULT_CONFIG)), \
                patch.object(self.main.beszel, "snapshot", AsyncMock(return_value={"systems": []})), \
                patch.object(self.main, "safe_weather", AsyncMock(return_value={"status": "weather"})), \
                patch.object(self.main, "safe_terminal", AsyncMock(return_value={"status": "terminal", "lines": ["log"]})), \
                patch.object(self.main, "snmp_feed", SimpleNamespace(packet=lambda: {"wan": {"status": "snmp"}})):
            result = await self.main.snapshot()
        self.assertEqual(result["wan"]["status"], "snmp")
        self.assertEqual(result["weather"]["status"], "weather")
        self.assertEqual(result["terminal"]["lines"], ["log"])
