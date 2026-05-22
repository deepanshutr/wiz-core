"""ESP-TOUCH onboarding handler: bridges HTTP and the esptouch library.

Owns discovery + registry access so the esptouch library stays
protocol-pure and reusable by tuya-core. Maps an onboarding run to the
amendment §A1 handler contract (200 / 408 / 500); HTTP 422 is handled
upstream by pydantic validation of OnboardIn.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from esptouch import EsptouchError, NewBulb
from esptouch import run as esptouch_run

from wiz_core.registry import Registry

log = logging.getLogger(__name__)

# Placeholder BSSID used when the host's AP BSSID cannot be determined.
# ESP-TOUCH still works without the real BSSID on most ESP firmware; the
# field is part of the datum but devices in setup mode accept a zero
# BSSID and fall back to the broadcast SSID match.
_UNKNOWN_BSSID = "000000000000"

OnboardStatus = Literal["ok", "timeout", "error"]


@dataclass
class OnboardResult:
    """Outcome of an onboarding run, mapped to HTTP by the route layer."""

    status: OnboardStatus
    onboarded: list[dict[str, Any]] = field(default_factory=list)
    attempted_seconds: int | None = None
    detail: str | None = None

    @classmethod
    def from_bulbs(cls, bulbs: list[NewBulb]) -> OnboardResult:
        """Build an 'ok' result from joined bulbs (§A1 200 payload)."""
        return cls(
            status="ok",
            onboarded=[
                {"mac": b.mac, "ip": b.ip, "name": b.name, "rssi": b.rssi}
                for b in bulbs
            ],
        )


def _local_ipv4() -> str:
    """Best-effort: this host's primary IPv4 on the LAN.

    Uses a connect()-to-discard trick on a UDP socket — no packet is
    actually sent and no DNS lookup happens (the target is a literal
    IP). Falls back to 0.0.0.0 if even that fails.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.1.255", 1))  # literal IP: no DNS, no traffic
        return str(sock.getsockname()[0])
    except OSError:
        return "0.0.0.0"
    finally:
        sock.close()


async def onboard(
    *,
    ssid: str,
    password: str,
    timeout_s: int,
    registry: Registry,
    run_discovery: Callable[[], Awaitable[int]],
) -> OnboardResult:
    """Run an ESP-TOUCH onboarding cycle and report what joined.

    Snapshots the registry's current MAC set, broadcasts credentials via
    esptouch.run(), and on each poll runs discovery and diffs the MAC
    set. Returns an OnboardResult the route layer maps to 200/408/500.
    """
    known_before: set[str] = {b.mac for b in registry.all()}

    async def on_join() -> list[NewBulb]:
        """Poll callback: run discovery, return MACs new since start."""
        try:
            await run_discovery()
        except Exception as exc:  # discovery is best-effort during onboarding
            log.debug("onboard discovery poll failed: %r", exc)
            return []
        fresh: list[NewBulb] = []
        for b in registry.all():
            if b.mac not in known_before:
                fresh.append(
                    NewBulb(mac=b.mac, ip=b.last_ip, name=b.name, rssi=b.last_rssi)
                )
        return fresh

    try:
        joined = await esptouch_run(
            ssid,
            password,
            timeout_s=timeout_s,
            on_join=on_join,
            bssid=_UNKNOWN_BSSID,
            local_ip=_local_ipv4(),
        )
    except EsptouchError as exc:
        log.warning("onboard failed: %r", exc)
        return OnboardResult(status="error", detail=str(exc))

    if joined:
        return OnboardResult.from_bulbs(joined)
    return OnboardResult(status="timeout", attempted_seconds=timeout_s)
