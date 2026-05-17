"""FastAPI route smoke tests using TestClient + a stub BulbClient."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from wiz_core.api import create_app
from wiz_core.bulb import BulbError
from wiz_core.registry import Registry


class StubClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.pilot: dict[str, Any] = {"state": True, "dimming": 100, "temp": 2200}

    async def get_pilot(self, ip: str) -> dict:
        self.calls.append(("get_pilot", ip, {}))
        return self.pilot

    async def set_pilot(self, ip: str, **params: Any) -> dict:
        self.calls.append(("set_pilot", ip, params))
        return {"success": True}


@pytest.fixture()
def client(tmp_path: Path) -> tuple[TestClient, Registry, StubClient]:
    reg = Registry(tmp_path / "state.json")
    reg.upsert_discovered({"mac": "d8a0118dc5c3", "ip": "192.168.1.3", "rssi": -63})
    stub = StubClient()

    async def fake_discover() -> int:
        return 0

    app = create_app(registry=reg, bulb=stub, run_discovery=fake_discover)
    return TestClient(app), reg, stub


def test_health(client) -> None:
    c, *_ = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_list_bulbs(client) -> None:
    c, *_ = client
    r = c.get("/bulbs")
    assert r.status_code == 200
    body = r.json()
    assert len(body["bulbs"]) == 1
    assert body["bulbs"][0]["mac"] == "d8a0118dc5c3"


def test_get_bulb_by_mac(client) -> None:
    c, _, stub = client
    r = c.get("/bulb/d8a0118dc5c3")
    assert r.status_code == 200
    assert r.json()["state"] is True
    assert stub.calls[0] == ("get_pilot", "192.168.1.3", {})


def test_get_bulb_by_name(client) -> None:
    c, reg, _ = client
    reg.rename("d8a0118dc5c3", "bedroom")
    r = c.get("/bulb/bedroom")
    assert r.status_code == 200


def test_get_bulb_404(client) -> None:
    c, *_ = client
    r = c.get("/bulb/notathing")
    assert r.status_code == 404


def test_get_bulb_default_via_default_endpoint(client) -> None:
    c, *_ = client
    r = c.get("/bulbs/default")
    assert r.status_code == 200
    assert r.json()["state"] is True


def test_on_off(client) -> None:
    c, _, stub = client
    r = c.post("/bulb/d8a0118dc5c3/on")
    assert r.status_code == 200
    assert stub.calls[-1] == ("set_pilot", "192.168.1.3", {"state": True})
    r = c.post("/bulb/d8a0118dc5c3/off")
    assert stub.calls[-1] == ("set_pilot", "192.168.1.3", {"state": False})


def test_brightness_validation(client) -> None:
    c, *_ = client
    r = c.post("/bulb/d8a0118dc5c3/brightness", json={"level": 999})
    assert r.status_code == 422
    r = c.post("/bulb/d8a0118dc5c3/brightness", json={"level": 50})
    assert r.status_code == 200


def test_temp_validation(client) -> None:
    c, *_ = client
    r = c.post("/bulb/d8a0118dc5c3/temp", json={"kelvin": 1000})
    assert r.status_code == 422
    r = c.post("/bulb/d8a0118dc5c3/temp", json={"kelvin": 3500})
    assert r.status_code == 200


def test_color(client) -> None:
    c, _, stub = client
    r = c.post("/bulb/d8a0118dc5c3/color", json={"r": 255, "g": 0, "b": 100})
    assert r.status_code == 200
    assert stub.calls[-1][2] == {"r": 255, "g": 0, "b": 100}


def test_scene_by_name(client) -> None:
    c, _, stub = client
    r = c.post("/bulb/d8a0118dc5c3/scene", json={"scene": "cozy"})
    assert r.status_code == 200
    assert stub.calls[-1][2] == {"sceneId": 6}


def test_scene_unknown(client) -> None:
    c, *_ = client
    r = c.post("/bulb/d8a0118dc5c3/scene", json={"scene": "nope"})
    assert r.status_code == 400


def test_rename(client) -> None:
    c, reg, _ = client
    r = c.post("/bulb/d8a0118dc5c3/name", json={"name": "kitchen"})
    assert r.status_code == 200
    assert reg.resolve("kitchen") is not None


def test_scenes_list(client) -> None:
    c, *_ = client
    r = c.get("/scenes")
    assert r.status_code == 200
    body = r.json()
    assert body["scenes"][5]["id"] == 6  # cozy
    assert body["scenes"][5]["name"] == "cozy"
    assert len(body["scenes"]) == 32


def test_empty_registry_409_on_default(tmp_path: Path) -> None:
    reg = Registry(tmp_path / "state.json")

    async def fake_discover() -> int:
        return 0

    app = create_app(registry=reg, bulb=StubClient(), run_discovery=fake_discover)
    c = TestClient(app)
    r = c.get("/bulbs/default")
    assert r.status_code == 409


def test_default_sentinel_route(client) -> None:
    """Confirm /bulb/_default also works (route-level pass-through to Registry.resolve)."""
    c, *_ = client
    r = c.get("/bulb/_default")
    assert r.status_code == 200
    assert r.json()["state"] is True


def test_discover_endpoint(client) -> None:
    c, *_ = client
    r = c.post("/discover", json={"passive": False})
    assert r.status_code == 200
    body = r.json()
    assert body["discovered"] == 0
    assert body["total"] == 1


def test_bulb_error_returns_504(tmp_path: Path) -> None:
    """A BulbError from get_pilot or set_pilot surfaces as HTTP 504."""

    class FailingClient(StubClient):
        async def set_pilot(self, ip: str, **params: Any) -> dict:
            raise BulbError("simulated UDP timeout")

        async def get_pilot(self, ip: str) -> dict:
            raise BulbError("simulated UDP timeout")

    reg = Registry(tmp_path / "state.json")
    reg.upsert_discovered({"mac": "d8a0118dc5c3", "ip": "192.168.1.3", "rssi": -63})

    async def fake_discover() -> int:
        return 0

    app = create_app(registry=reg, bulb=FailingClient(), run_discovery=fake_discover)
    c = TestClient(app)
    r = c.post("/bulb/d8a0118dc5c3/on")
    assert r.status_code == 504
    assert "simulated UDP timeout" in r.json()["detail"]

    r = c.get("/bulb/d8a0118dc5c3")
    assert r.status_code == 504

    r = c.get("/bulbs/default")
    assert r.status_code == 504
