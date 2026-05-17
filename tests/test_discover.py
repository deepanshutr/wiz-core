"""Discovery: broadcast then unicast-sweep fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from philips_wiz_bulb_core.bulb import BulbError
from philips_wiz_bulb_core.discover import discover, parse_discovery_response


def test_parse_response_extracts_mac_module() -> None:
    raw = {
        "method": "getPilot", "env": "pro",
        "result": {"mac": "d8a0118dc5c3", "rssi": -63, "state": True, "temp": 2200, "dimming": 100},
    }
    parsed = parse_discovery_response(raw, ip="192.168.1.3")
    assert parsed["mac"] == "d8a0118dc5c3"
    assert parsed["ip"] == "192.168.1.3"
    assert parsed["rssi"] == -63


def test_parse_response_missing_mac_raises() -> None:
    with pytest.raises(ValueError, match="missing mac"):
        parse_discovery_response({"result": {}}, ip="192.168.1.3")


async def test_discover_unicast_sweep_runs_on_every_host() -> None:
    """Sweep probes every host in the subnet via the BulbClient."""
    probed: list[str] = []

    async def fake_get_pilot(ip: str) -> dict:
        probed.append(ip)
        if ip == "192.168.1.2":
            return {"mac": "aabbccddeeff", "rssi": -50}
        raise TimeoutError("nope")

    client = AsyncMock()
    client.get_pilot = AsyncMock(side_effect=fake_get_pilot)

    # Patch broadcast collect to a no-op so we exercise only the sweep path
    with patch("philips_wiz_bulb_core.discover._broadcast_collect", AsyncMock(return_value=[])):
        bulbs = await discover(
            client,
            broadcast="192.168.1.255",
            subnet="192.168.1.0/30",  # hosts: .1, .2
            broadcast_collect_s=0.0,
            sweep_client=client,
        )

    assert sorted(probed) == ["192.168.1.1", "192.168.1.2"]
    assert len(bulbs) == 1
    assert bulbs[0]["mac"] == "aabbccddeeff"
    assert bulbs[0]["ip"] == "192.168.1.2"


async def test_discover_broadcast_wins_on_mac_collision() -> None:
    """When the same MAC appears in broadcast AND sweep, broadcast's record wins."""
    broadcast_bulb = {"mac": "aabbccddeeff", "ip": "192.168.1.50", "rssi": -40}

    async def fake_get_pilot(ip: str) -> dict:
        # Sweep also finds the same MAC, but at a different (stale) IP
        return {"mac": "aabbccddeeff", "rssi": -99}

    client = AsyncMock()
    client.get_pilot = AsyncMock(side_effect=fake_get_pilot)

    with patch(
        "philips_wiz_bulb_core.discover._broadcast_collect",
        AsyncMock(return_value=[broadcast_bulb]),
    ):
        bulbs = await discover(
            client,
            broadcast="192.168.1.255",
            subnet="192.168.1.0/30",
            broadcast_collect_s=0.0,
            sweep_client=client,
        )

    assert len(bulbs) == 1
    # Broadcast IP wins (192.168.1.50, not whatever sweep IP)
    assert bulbs[0]["ip"] == "192.168.1.50"
    assert bulbs[0]["rssi"] == -40


async def test_discover_sweep_probe_errors_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A BulbError on a sweep probe is logged at debug, not propagated."""
    import logging

    async def fake_get_pilot(ip: str) -> dict:
        raise BulbError("simulated bulb fail")

    client = AsyncMock()
    client.get_pilot = AsyncMock(side_effect=fake_get_pilot)

    with patch("philips_wiz_bulb_core.discover._broadcast_collect", AsyncMock(return_value=[])):
        caplog.set_level(logging.DEBUG, logger="philips_wiz_bulb_core.discover")
        bulbs = await discover(
            client,
            broadcast="192.168.1.255",
            subnet="192.168.1.0/30",
            broadcast_collect_s=0.0,
            sweep_client=client,
        )

    assert bulbs == []
    assert any("sweep probe to" in rec.message for rec in caplog.records)
