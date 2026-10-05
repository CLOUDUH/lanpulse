import hashlib
import json
import os
import secrets
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "beszel": {
        "url": "http://192.168.1.83:8090",
        "email": "",
        "password": "",
        "verify_tls": True,
        "poll_seconds": 5,
    },
    "snmp": {
        "enabled": False,
        "host": "",
        "port": 161,
        "community": "",
        "version": "2c",
        "interface_index": 0,
        "upload_oid": "1.3.6.1.2.1.31.1.1.1.10.{ifIndex}",
        "download_oid": "1.3.6.1.2.1.31.1.1.1.6.{ifIndex}",
        "poll_seconds": 2,
        "timeout_seconds": 2,
    },
    "weather": {
        "enabled": True,
        "location": "北京",
        "latitude": 39.9042,
        "longitude": 116.4074,
        "refresh_minutes": 30,
    },
    "terminal": {
        "enabled": False,
        "host": "",
        "port": 22,
        "username": "",
        "password": "",
        "private_key": "",
        "key_passphrase": "",
        "verify_host_key": False,
        "known_hosts": "",
        "command": "journalctl -n 80 -u training.service --no-pager -o cat",
        "max_lines": 80,
        "timeout_seconds": 5,
    },
    "display": {
        "title": "LanPulse",
        "orientation": "auto",
        "rotation": 0,
        "theme": "dark",
        "history_points": 60,
    },
    "security": {"pin_salt": "", "pin_hash": ""},
}


class ConfigStore:
    def __init__(self, data_dir: str | None = None):
        self.data_dir = Path(data_dir or os.getenv("DATA_DIR", "/data"))
        self.path = self.data_dir / "config.json"
        self.lock = threading.RLock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save(deepcopy(DEFAULT_CONFIG))

    def load(self) -> dict[str, Any]:
        with self.lock:
            try:
                current = json.loads(self.path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                current = {}
            merged = deepcopy(DEFAULT_CONFIG)
            for section, values in current.items():
                if isinstance(values, dict) and isinstance(merged.get(section), dict):
                    merged[section].update(values)
                else:
                    merged[section] = values
            return merged

    def save(self, data: dict[str, Any]) -> None:
        with self.lock:
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
            os.chmod(temp, 0o600)
            temp.replace(self.path)

    def public(self) -> dict[str, Any]:
        data = self.load()
        data["beszel"]["password"] = ""
        data["beszel"]["password_set"] = bool(self.load()["beszel"].get("password"))
        data["snmp"]["community"] = ""
        data["snmp"]["community_set"] = bool(self.load()["snmp"].get("community"))
        stored_terminal = self.load()["terminal"]
        for key in ("password", "private_key", "key_passphrase"):
            data["terminal"][key] = ""
            data["terminal"][f"{key}_set"] = bool(stored_terminal.get(key))
        data["security"] = {"pin_set": bool(data["security"].get("pin_hash"))}
        return data

    def update(self, incoming: dict[str, Any]) -> dict[str, Any]:
        data = self.load()
        secret_fields = {"password", "community", "private_key", "key_passphrase"}
        for section in ("beszel", "snmp", "weather", "terminal", "display"):
            values = incoming.get(section)
            if not isinstance(values, dict):
                continue
            allowed = set(DEFAULT_CONFIG[section])
            for key, value in values.items():
                if key in allowed and not (key in secret_fields and value == ""):
                    data[section][key] = value
        pin = incoming.get("admin_pin")
        if isinstance(pin, str) and pin:
            if len(pin) < 4:
                raise ValueError("管理 PIN 至少需要 4 位")
            salt = secrets.token_hex(16)
            data["security"] = {"pin_salt": salt, "pin_hash": self._hash_pin(pin, salt)}
        self.save(data)
        return self.public()

    def verify_pin(self, pin: str) -> bool:
        sec = self.load()["security"]
        if not sec.get("pin_hash"):
            return True
        return secrets.compare_digest(self._hash_pin(pin, sec["pin_salt"]), sec["pin_hash"])

    @staticmethod
    def _hash_pin(pin: str, salt: str) -> str:
        return hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
