"""LAN discovery for WiZ bulbs."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
from typing import Any

from .bulb import DEFAULT_PORT, BulbClient, BulbError

log = logging.getLogger(__name__)


def parse_discovery_response(raw: dict[str, Any], ip: str) -> dict[str, Any]:
    """Normalise a getPilot reply for the registry."""
    result = raw.get("result", {})
    mac = str(result.get("mac", "")).lower().replace(":", "")
    if not mac:
        raise ValueError(f"discovery reply from {ip} missing mac: {raw!r}")
    return {
        "mac": mac,
        "ip": ip,
        "rssi": result.get("rssi"),
        "state": result.get("state"),
        "temp": result.get("temp"),
        "dimming": result.get("dimming"),
    }


async def _broadcast_collect(
    broadcast: str, port: int, collect_s: float
) -> list[dict[str, Any]]:
    """Send one broadcast getPilot, collect responses for collect_s seconds."""
    loop = asyncio.get_running_loop()
    payload = json.dumps({"method": "getPilot", "params": {}}).encode()
    received: dict[str, dict[str, Any]] = {}  # ip -> reply

    class _Collector(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            try:
                received[addr[0]] = json.loads(data.decode())
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                log.debug("dropping unparseable datagram from %s: %r", addr[0], exc)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    sock.bind(("0.0.0.0", 0))
    transport, _ = await loop.create_datagram_endpoint(_Collector, sock=sock)
    try:
        transport.sendto(payload, (broadcast, port))
        await asyncio.sleep(collect_s)
    finally:
        transport.close()

    out: list[dict[str, Any]] = []
    for ip, raw in received.items():
        try:
            out.append(parse_discovery_response(raw, ip))
        except ValueError:
            continue
    return out


async def _sweep_subnet(
    client: BulbClient, subnet: str, concurrency: int = 64
) -> list[dict[str, Any]]:
    """Unicast getPilot to every host in the subnet, in parallel batches."""
    net = ipaddress.ip_network(subnet, strict=False)
    hosts = [str(h) for h in net.hosts()]
    out: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(concurrency)

    async def probe(ip: str) -> None:
        async with sem:
            try:
                raw_result = await client.get_pilot(ip)
            except (BulbError, TimeoutError, OSError) as exc:
                log.debug("sweep probe to %s failed: %r", ip, exc)
                return
            # Unicast get_pilot returns just .result; wrap to reuse parser.
            try:
                out.append(parse_discovery_response({"result": raw_result}, ip))
            except ValueError:
                pass

    await asyncio.gather(*(probe(ip) for ip in hosts))
    return out


async def discover(
    client: BulbClient,
    *,
    broadcast: str,
    subnet: str,
    broadcast_collect_s: float = 3.0,
    sweep_client: BulbClient | None = None,
) -> list[dict[str, Any]]:
    """Discover bulbs: broadcast first, then unicast-sweep to catch AP isolation.

    Returns a list of normalised bulb dicts deduped by MAC (broadcast wins).

    `client` is used for general daemon-side calls (typically the same
    BulbClient instance the rest of the app uses, with retries enabled).
    `sweep_client` is used for the speculative subnet sweep; when omitted,
    a dedicated retries=0 client is constructed because retrying empty IPs
    turns a 9s sweep into a 30s+ sweep without finding more bulbs. Tests
    can inject `sweep_client=mock` to verify probe behaviour.
    """
    if sweep_client is None:
        sweep_client = BulbClient(retries=0, timeout=1.5)
    seen: dict[str, dict[str, Any]] = {}
    for bulb in await _broadcast_collect(broadcast, DEFAULT_PORT, broadcast_collect_s):
        seen[bulb["mac"]] = bulb
    for bulb in await _sweep_subnet(sweep_client, subnet):
        seen.setdefault(bulb["mac"], bulb)
    return list(seen.values())
