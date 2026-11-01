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
        self._lock = asyncio.Lock()

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
        engine = SnmpEngine()
        try:
            target = await self._target(cfg)
            result = await get_cmd(
                engine,
                CommunityData(cfg.get("community", "public"), mpModel=1),
                target,
                ContextData(),
                ObjectType(ObjectIdentity(self._oid(cfg["download_oid"], index))),
                ObjectType(ObjectIdentity(self._oid(cfg["upload_oid"], index))),
            )
        finally:
            engine.close_dispatcher()
        error_indication, error_status, _, bindings = result
        if error_indication:
            raise RuntimeError(str(error_indication))
        if error_status:
            raise RuntimeError(error_status.prettyPrint())
        return int(bindings[0][1]), int(bindings[1][1])

    async def sample(self, cfg: dict[str, Any]) -> dict[str, Any]:
        if not cfg.get("enabled") or not cfg.get("host"):
            return {"enabled": False, "status": "disabled", "download_bps": None, "upload_bps": None}
        key = tuple(cfg.get(field) for field in (
            "host", "port", "interface_index", "community", "download_oid", "upload_oid",
        ))
        interval = max(1, float(cfg.get("poll_seconds", 2)))
        # All dashboards share one counter baseline and one result per polling interval.
        async with self._lock:
            cached = self.latest.get(key)
            if cached and time.monotonic() - cached[0] < interval:
                return dict(cached[1])
            result: dict[str, Any] = {
                "enabled": True, "status": "warming", "download_bps": None,
                "upload_bps": None, "poll_seconds": interval,
            }
            try:
                down, up = await self._get_pair(cfg)
                now = time.monotonic()
                previous = self.previous.get(key)
                self.previous[key] = (now, down, up)
                # A restart/interface reset must not be mistaken for a 64-bit wrap.
                if previous and down >= previous[1] and up >= previous[2]:
                    elapsed = max(now - previous[0], 0.001)
                    result.update(
                        status="up",
                        download_bps=(down - previous[1]) * 8 / elapsed,
                        upload_bps=(up - previous[2]) * 8 / elapsed,
                    )
            except (RuntimeError, OSError, asyncio.TimeoutError, ValueError) as exc:
                result.update(status="error", error=str(exc))
            result["sampled_at"] = time.time()
            self.latest[key] = (time.monotonic(), result)
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


async def safe_sample(collector: SnmpCollector, cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        return await collector.sample(cfg)
    except (RuntimeError, OSError, asyncio.TimeoutError) as exc:
        return {"enabled": True, "status": "error", "error": str(exc), "download_bps": None, "upload_bps": None, "sampled_at": time.time()}

