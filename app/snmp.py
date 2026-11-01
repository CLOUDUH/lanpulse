import asyncio
import time
from typing import Any

from pysnmp.hlapi.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    get_cmd,
    walk_cmd,
)


IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"


class SnmpCollector:
    def __init__(self) -> None:
        self.previous: dict[tuple, tuple[float, int, int]] = {}
        self.latest: dict[tuple, tuple[float, dict[str, Any]]] = {}
        self.counter_state: dict[tuple, list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        self._engine: SnmpEngine | None = None
        self._transport = None
        self._transport_key: tuple | None = None

    async def close(self) -> None:
        async with self._lock:
            if self._engine is not None:
                self._engine.close_dispatcher()
            self._engine = None
            self._transport = None
            self._transport_key = None

    async def reset(self) -> None:
        await self.close()
        self.previous.clear()
        self.latest.clear()
        self.counter_state.clear()

    @staticmethod
    def _rate(state: dict[str, Any], counter: int, now: float, elapsed: float,
              interval: float, window: float) -> tuple[float, bool]:
        changed_elapsed = max(now - state["changed_at"], 0.001)
        if counter == state["counter"]:
            # Identical counters do not tell us whether traffic stopped or the
            # agent has not refreshed yet. Briefly hold the last estimate and
            # expose that fact, bounded by the observed refresh cadence.
            hold = min(window, max(interval * 1.5, state["period"] * 1.25))
            held = bool(state.get("rate") and changed_elapsed <= hold)
            return (state["rate"] if held else 0), held
        duration = changed_elapsed if window and changed_elapsed <= window else elapsed
        rate = (counter - state["counter"]) * 8 / duration
        state.update(counter=counter, changed_at=now, rate=rate,
                     period=duration if window and changed_elapsed <= window else interval)
        return rate, False

    @staticmethod
    def _oid(template: str, index: int) -> str:
        return template.replace("{ifIndex}", str(index))

    async def _target(self, cfg: dict[str, Any]):
        return await UdpTransportTarget.create(
            (cfg["host"], int(cfg.get("port", 161))),
            timeout=float(cfg.get("timeout_seconds", 2)),
            retries=0,
        )

    async def _get_pair(self, cfg: dict[str, Any]) -> tuple[int, int]:
        index = int(cfg.get("interface_index", 0))
        if index <= 0:
            raise RuntimeError("请先发现并选择爱快 WAN 接口")
        if self._engine is None:
            self._engine = SnmpEngine()
        transport_key = (cfg["host"], int(cfg.get("port", 161)), float(cfg.get("timeout_seconds", 2)))
        if self._transport is None or transport_key != self._transport_key:
            self._transport = await self._target(cfg)
            self._transport_key = transport_key
        result = await get_cmd(
            self._engine,
            CommunityData(cfg.get("community", "public"), mpModel=1),
            self._transport,
            ContextData(),
            ObjectType(ObjectIdentity(self._oid(cfg["download_oid"], index))),
            ObjectType(ObjectIdentity(self._oid(cfg["upload_oid"], index))),
        )
        error_indication, error_status, _, bindings = result
        if error_indication:
            raise RuntimeError(str(error_indication))
        if error_status:
            raise RuntimeError(error_status.prettyPrint())
        return int(bindings[0][1]), int(bindings[1][1])

    async def sample(self, cfg: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        if not cfg.get("enabled") or not cfg.get("host"):
            return {"enabled": False, "status": "disabled", "download_bps": None, "upload_bps": None}
        key = tuple(cfg.get(field) for field in (
            "host", "port", "interface_index", "community", "download_oid", "upload_oid", "counter_window_seconds",
        ))
        interval = max(1, float(cfg.get("poll_seconds", 1)))
        # All dashboards share one counter baseline and one result per polling interval.
        async with self._lock:
            cached = self.latest.get(key)
            if not force and cached and time.monotonic() - cached[0] < interval:
                return dict(cached[1])
            started = time.monotonic()
            result: dict[str, Any] = {
                "enabled": True, "status": "warming", "download_bps": None,
                "upload_bps": None, "poll_seconds": interval,
            }
            try:
                down, up = await self._get_pair(cfg)
                now = time.monotonic()
                result.update(download_counter=down, upload_counter=up,
                              poll_duration_ms=round((now - started) * 1000, 1))
                previous = self.previous.get(key)
                self.previous[key] = (now, down, up)
                # A restart/interface reset must not be mistaken for a 64-bit wrap.
                if previous and down >= previous[1] and up >= previous[2]:
                    elapsed = max(now - previous[0], 0.001)
                    window = max(0, min(30, float(cfg.get("counter_window_seconds", 10))))
                    states = self.counter_state[key]
                    down_rate, down_held = self._rate(states[0], down, now, elapsed, interval, window)
                    up_rate, up_held = self._rate(states[1], up, now, elapsed, interval, window)
                    result.update(
                        status="up",
                        interval_seconds=elapsed,
                        raw_download_bps=(down - previous[1]) * 8 / elapsed,
                        raw_upload_bps=(up - previous[2]) * 8 / elapsed,
                        download_bps=down_rate,
                        upload_bps=up_rate,
                        download_held=down_held,
                        upload_held=up_held,
                    )
                else:
                    self.counter_state[key] = [
                        {"counter": counter, "changed_at": now, "rate": None, "period": interval}
                        for counter in (down, up)
                    ]
            except (RuntimeError, OSError, asyncio.TimeoutError, ValueError) as exc:
                result.update(status="error", error=str(exc))
            result["sampled_at"] = time.time()
            self.latest[key] = (started, result)
            return dict(result)

    async def discover(self, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        if not cfg.get("host") or not cfg.get("community"):
            raise RuntimeError("请先填写 SNMP 地址和团体名")
        engine = SnmpEngine()
        target = await self._target(cfg)
        interfaces: dict[int, dict[str, Any]] = {}
        for base_oid, field in ((IF_NAME, "name"), (IF_ALIAS, "alias")):
            async for error_indication, error_status, _, bindings in walk_cmd(
                engine,
                CommunityData(cfg.get("community", "public"), mpModel=1),
                target,
                ContextData(),
                ObjectType(ObjectIdentity(base_oid)),
                lexicographicMode=False,
            ):
                if error_indication:
                    engine.close_dispatcher()
                    raise RuntimeError(str(error_indication))
                if error_status:
                    engine.close_dispatcher()
                    raise RuntimeError(error_status.prettyPrint())
                for oid, value in bindings:
                    index = int(str(oid).split(".")[-1])
                    interfaces.setdefault(index, {"index": index, "name": "", "alias": ""})[field] = value.prettyPrint()
        engine.close_dispatcher()
        return sorted(interfaces.values(), key=lambda item: item["index"])


async def safe_sample(collector: SnmpCollector, cfg: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    try:
        return await collector.sample(cfg, force=True) if force else await collector.sample(cfg)
    except (RuntimeError, OSError, asyncio.TimeoutError) as exc:
        return {"enabled": True, "status": "error", "error": str(exc), "download_bps": None, "upload_bps": None, "sampled_at": time.time()}

