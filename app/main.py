import asyncio
import json
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .beszel import BeszelClient
from .calendar_info import beijing_calendar
from .config import ConfigStore
from .snmp import SnmpCollector, safe_sample
from .terminal import TerminalClient, safe_terminal
from .weather import WeatherClient, safe_weather


STATIC = Path(__file__).resolve().parent / "static"
store = ConfigStore()
beszel = BeszelClient()
snmp = SnmpCollector()
weather = WeatherClient()
terminal = TerminalClient()
sessions: dict[str, float] = {}
app = FastAPI(title="LanPulse", version="1.0.0", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class LoginBody(BaseModel):
    pin: str = ""


def authorized(token: str | None) -> bool:
    config = store.load()
    if not config["security"].get("pin_hash"):
        return True
    if not token or token not in sessions:
        return False
    if sessions[token] < time.time():
        sessions.pop(token, None)
        return False
    return True


def require_auth(token: str | None) -> None:
    if not authorized(token):
        raise HTTPException(401, "请先用管理 PIN 解锁设置")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/settings")
async def settings_page():
    return FileResponse(STATIC / "settings.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "time": time.time()}


@app.post("/api/login")
async def login(body: LoginBody):
    if not store.verify_pin(body.pin):
        raise HTTPException(401, "PIN 不正确")
    token = secrets.token_urlsafe(32)
    sessions[token] = time.time() + 12 * 3600
    return {"token": token, "expires_in": 43200}


@app.get("/api/config")
async def get_config(x_admin_token: str | None = Header(default=None)):
    require_auth(x_admin_token)
    return store.public()


@app.put("/api/config")
async def put_config(request: Request, x_admin_token: str | None = Header(default=None)):
    require_auth(x_admin_token)
    try:
        return store.update(await request.json())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/test/beszel")
async def test_beszel(x_admin_token: str | None = Header(default=None)):
    require_auth(x_admin_token)
    try:
        return await beszel.test(store.load()["beszel"])
    except Exception as exc:
        raise HTTPException(502, f"Beszel 连接失败：{exc}") from exc


@app.post("/api/snmp/interfaces")
async def snmp_interfaces(x_admin_token: str | None = Header(default=None)):
    require_auth(x_admin_token)
    try:
        return {"items": await snmp.discover(store.load()["snmp"])}
    except Exception as exc:
        raise HTTPException(502, f"SNMP 发现失败：{exc}") from exc


@app.post("/api/test/terminal")
async def test_terminal(x_admin_token: str | None = Header(default=None)):
    require_auth(x_admin_token)
    result = await safe_terminal(terminal, store.load()["terminal"])
    if result.get("status") != "up":
        raise HTTPException(502, result.get("error") or "Ubuntu 日志命令执行失败")
    lines = result.get("lines", [])
    return {"ok": True, "lines": len(lines), "preview": lines[-3:]}


@app.get("/api/snapshot")
async def snapshot():
    config = store.load()
    results = await asyncio.gather(
        beszel.snapshot(config["beszel"]),
        safe_sample(snmp, config["snmp"]),
        safe_weather(weather, config["weather"]),
        safe_terminal(terminal, config["terminal"]),
        return_exceptions=True,
    )
    beszel_data = results[0] if not isinstance(results[0], Exception) else {"systems": [], "error": str(results[0])}
    snmp_data = results[1] if not isinstance(results[1], Exception) else {"status": "error", "error": str(results[1])}
    weather_data = results[2] if not isinstance(results[2], Exception) else {"status": "error", "error": str(results[2])}
    terminal_data = results[3] if not isinstance(results[3], Exception) else {"status": "error", "error": str(results[3]), "lines": []}
    now = time.time()
    return {"beszel": beszel_data, "wan": snmp_data, "weather": weather_data, "terminal": terminal_data, "calendar": beijing_calendar(now), "display": config["display"], "server_time": now}


@app.get("/api/events")
async def events(request: Request):
    async def stream():
        while not await request.is_disconnected():
            config = store.load()
            results = await asyncio.gather(
                beszel.snapshot(config["beszel"]),
                safe_sample(snmp, config["snmp"]),
                safe_weather(weather, config["weather"]),
                safe_terminal(terminal, config["terminal"]),
                return_exceptions=True,
            )
            now = time.time()
            payload: dict[str, Any] = {
                "beszel": results[0] if not isinstance(results[0], Exception) else {"systems": [], "error": str(results[0])},
                "wan": results[1] if not isinstance(results[1], Exception) else {"status": "error", "error": str(results[1])},
                "weather": results[2] if not isinstance(results[2], Exception) else {"status": "error", "error": str(results[2])},
                "terminal": results[3] if not isinstance(results[3], Exception) else {"status": "error", "error": str(results[3]), "lines": []},
                "calendar": beijing_calendar(now),
                "display": config["display"],
                "server_time": now,
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            seconds = min(float(config["beszel"].get("poll_seconds", 5)), float(config["snmp"].get("poll_seconds", 2)))
            await asyncio.sleep(max(seconds, 1))
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
