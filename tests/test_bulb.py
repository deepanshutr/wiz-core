"""WiZ JSON-over-UDP client."""

from __future__ import annotations

import asyncio
import json

import pytest

from wiz_core.bulb import BulbClient, BulbError


class FakeBulbServer:
    """Listens on 127.0.0.1:0 and echoes canned responses."""

    def __init__(self, responses: list[dict | None]) -> None:
        self.responses = list(responses)
        self.received: list[dict] = []
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport):  # type: ignore[no-untyped-def]
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr):  # type: ignore[no-untyped-def]
        self.received.append(json.loads(data.decode()))
        nxt = self.responses.pop(0) if self.responses else None
        if nxt is None:
            return  # silent drop -> timeout path
        assert self.transport is not None
        self.transport.sendto(json.dumps(nxt).encode(), addr)

    def connection_lost(self, exc):  # type: ignore[no-untyped-def]
        pass


@pytest.fixture()
async def fake_bulb():
    loop = asyncio.get_running_loop()
    responses: list[dict | None] = []
    server = FakeBulbServer(responses)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: server, local_addr=("127.0.0.1", 0)
    )
    _, port = transport.get_extra_info("sockname")
    try:
        yield server, "127.0.0.1", port
    finally:
        transport.close()


async def test_get_pilot_happy(fake_bulb) -> None:
    server, ip, port = fake_bulb
    server.responses.append({
        "method": "getPilot", "env": "pro",
        "result": {"mac": "abc", "state": True, "dimming": 100, "temp": 2700},
    })
    c = BulbClient(port=port, timeout=0.5, retries=0)
    out = await c.get_pilot(ip)
    assert out["state"] is True
    assert out["temp"] == 2700
    assert server.received[0]["method"] == "getPilot"


async def test_set_pilot_passes_params(fake_bulb) -> None:
    server, ip, port = fake_bulb
    server.responses.append({"method": "setPilot", "env": "pro", "result": {"success": True}})
    c = BulbClient(port=port, timeout=0.5, retries=0)
    await c.set_pilot(ip, state=True, dimming=50)
    sent = server.received[0]
    assert sent["method"] == "setPilot"
    assert sent["params"] == {"state": True, "dimming": 50}


async def test_timeout_raises(fake_bulb) -> None:
    server, ip, port = fake_bulb
    server.responses.append(None)  # drop -> timeout
    c = BulbClient(port=port, timeout=0.2, retries=0)
    with pytest.raises(BulbError, match="timeout"):
        await c.get_pilot(ip)


async def test_wiz_error_envelope(fake_bulb) -> None:
    server, ip, port = fake_bulb
    server.responses.append({
        "method": "setPilot", "env": "pro",
        "error": {"code": -32602, "message": "Invalid params"},
    })
    c = BulbClient(port=port, timeout=0.5, retries=0)
    with pytest.raises(BulbError, match="Invalid params"):
        await c.set_pilot(ip, state=True)


async def test_retries_then_success(fake_bulb) -> None:
    server, ip, port = fake_bulb
    server.responses.extend([
        None,  # first attempt drops
        {"method": "getPilot", "env": "pro", "result": {"state": False}},
    ])
    c = BulbClient(port=port, timeout=0.2, retries=1)
    out = await c.get_pilot(ip)
    assert out["state"] is False
    assert len(server.received) == 2
