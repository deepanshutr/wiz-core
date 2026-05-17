"""Per-MAC bulb registry, persisted to a 0600-mode state.json."""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _normalise_mac(s: str) -> str:
    return "".join(c for c in s.lower() if c in "0123456789abcdef")


@dataclass
class Bulb:
    mac: str
    name: str
    last_ip: str
    last_rssi: int | None = None
    module: str | None = None
    fw_version: str | None = None
    cct_range: tuple[int, int] | None = None
    discovered_at: str = field(default_factory=_now_iso)
    last_seen: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "last_ip": self.last_ip,
            "last_rssi": self.last_rssi,
            "module": self.module,
            "fw_version": self.fw_version,
            "discovered_at": self.discovered_at,
            "last_seen": self.last_seen,
        }
        if self.cct_range is not None:
            d["cct_range"] = list(self.cct_range)
        return d


class Registry:
    """In-memory registry backed by `path` (JSON, 0600)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._bulbs: dict[str, Bulb] = {}
        self._load()

    # ---- Persistence ----

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            blob = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return
        for mac, raw in blob.get("bulbs", {}).items():
            cct = raw.get("cct_range")
            self._bulbs[mac] = Bulb(
                mac=mac,
                name=raw.get("name", f"bulb-{len(self._bulbs) + 1}"),
                last_ip=raw["last_ip"],
                last_rssi=raw.get("last_rssi"),
                module=raw.get("module"),
                fw_version=raw.get("fw_version"),
                cct_range=(int(cct[0]), int(cct[-1])) if cct else None,
                discovered_at=raw.get("discovered_at", _now_iso()),
                last_seen=raw.get("last_seen", _now_iso()),
            )

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        blob = {
            "version": 1,
            "bulbs": {mac: b.to_dict() for mac, b in self._bulbs.items()},
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(blob, indent=2, sort_keys=True))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    # ---- Mutators ----

    def upsert_discovered(self, raw: dict[str, Any]) -> Bulb:
        """Idempotently add/refresh a bulb from a discovery dict."""
        mac = _normalise_mac(raw["mac"])
        ip = raw["ip"]
        now = _now_iso()
        if mac in self._bulbs:
            b = self._bulbs[mac]
            b.last_ip = ip
            b.last_seen = now
            if (rssi := raw.get("rssi")) is not None:
                b.last_rssi = rssi
        else:
            b = Bulb(
                mac=mac,
                name=f"bulb-{len(self._bulbs) + 1}",
                last_ip=ip,
                last_rssi=raw.get("rssi"),
                discovered_at=now,
                last_seen=now,
            )
            self._bulbs[mac] = b
        return b

    def enrich(self, mac: str, system_config: dict[str, Any]) -> None:
        """Fold getSystemConfig into the registry entry."""
        mac = _normalise_mac(mac)
        if mac not in self._bulbs:
            return
        b = self._bulbs[mac]
        b.module = system_config.get("moduleName") or b.module
        b.fw_version = system_config.get("fwVersion") or b.fw_version
        cct = system_config.get("cctRange")
        if cct and len(cct) >= 2:
            b.cct_range = (int(cct[0]), int(cct[-1]))

    def rename(self, mac: str, new_name: str) -> Bulb:
        mac = _normalise_mac(mac)
        if mac not in self._bulbs:
            raise KeyError(mac)
        b = self._bulbs[mac]
        b.name = new_name
        return b

    # ---- Lookups ----

    def all(self) -> list[Bulb]:
        return list(self._bulbs.values())

    def default(self) -> Bulb | None:
        if not self._bulbs:
            return None
        # Tie-break by insertion order: when discovered_at strings compare equal
        # (e.g. two upserts in the same microsecond), enumerate index keeps the
        # earliest-inserted bulb first. Python dicts preserve insertion order.
        indexed = enumerate(self._bulbs.values())
        return min(indexed, key=lambda pair: (pair[1].discovered_at, pair[0]))[1]

    def resolve(self, target: str | None) -> Bulb | None:
        """Resolve target per spec rules. None/empty/"_default" => default()."""
        if not target or target == "_default":
            return self.default()
        # Try MAC
        mac_try = _normalise_mac(target)
        if len(mac_try) == 12 and mac_try in self._bulbs:
            return self._bulbs[mac_try]
        # Try IP
        try:
            ip = str(ipaddress.IPv4Address(target))
            for b in self._bulbs.values():
                if b.last_ip == ip:
                    return b
        except (ipaddress.AddressValueError, ValueError):
            pass
        # Friendly name (case-insensitive)
        t = target.strip().lower()
        for b in self._bulbs.values():
            if b.name.lower() == t:
                return b
        return None
