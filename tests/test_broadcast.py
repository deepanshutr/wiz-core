"""Unit tests for the broadcast fan-out engine and the /bulb/all/{op} routes."""

from __future__ import annotations

import asyncio
from typing import Any

from wiz_core.broadcast import AllResult, BulbCallResult, run_all


def test_bulb_call_result_to_dict_ok() -> None:
    r = BulbCallResult(mac="d8a0118dc5c3", ok=True, duration_ms=38, error=None)
    assert r.to_dict() == {"mac": "d8a0118dc5c3", "ok": True, "duration_ms": 38}


def test_bulb_call_result_to_dict_failed_includes_error() -> None:
    r = BulbCallResult(mac="d8a011c0a795", ok=False, duration_ms=1500, error="udp_timeout")
    assert r.to_dict() == {
        "mac": "d8a011c0a795",
        "ok": False,
        "duration_ms": 1500,
        "error": "udp_timeout",
    }


def test_all_result_to_dict_shape() -> None:
    results = [
        BulbCallResult(mac="aaa", ok=True, duration_ms=10, error=None),
        BulbCallResult(mac="bbb", ok=False, duration_ms=20, error="boom"),
    ]
    out = AllResult(op="on", duration_ms=99, results=results).to_dict()
    assert out == {
        "op": "on",
        "total": 2,
        "ok": 1,
        "failed": 1,
        "duration_ms": 99,
        "results": [
            {"mac": "aaa", "ok": True, "duration_ms": 10},
            {"mac": "bbb", "ok": False, "duration_ms": 20, "error": "boom"},
        ],
    }


async def test_run_all_empty_targets() -> None:
    async def never_called(mac: str, ip: str) -> dict[str, Any]:
        raise AssertionError("should not be called for empty targets")

    result = await run_all(op="on", targets=[], call=never_called, concurrency=16)
    assert result.to_dict() == {
        "op": "on",
        "total": 0,
        "ok": 0,
        "failed": 0,
        "duration_ms": result.duration_ms,
        "results": [],
    }
    assert isinstance(result.duration_ms, int)
    assert result.duration_ms >= 0


# Keep a reference to asyncio so the import is not flagged unused before later tasks.
_ = asyncio


async def test_run_all_all_succeed() -> None:
    async def call(mac: str, ip: str) -> dict[str, Any]:
        return {"success": True}

    targets = [("aaa", "192.168.1.1"), ("bbb", "192.168.1.2")]
    result = await run_all(op="on", targets=targets, call=call, concurrency=16)
    out = result.to_dict()
    assert out["total"] == 2
    assert out["ok"] == 2
    assert out["failed"] == 0
    assert {r["mac"] for r in out["results"]} == {"aaa", "bbb"}
    assert all(r["ok"] is True for r in out["results"])
    assert all("error" not in r for r in out["results"])


async def test_run_all_exception_becomes_per_bulb_error() -> None:
    async def call(mac: str, ip: str) -> dict[str, Any]:
        if mac == "bbb":
            raise RuntimeError("simulated udp timeout")
        return {"success": True}

    targets = [("aaa", "192.168.1.1"), ("bbb", "192.168.1.2")]
    result = await run_all(op="off", targets=targets, call=call, concurrency=16)
    out = result.to_dict()
    assert out["total"] == 2
    assert out["ok"] == 1
    assert out["failed"] == 1
    by_mac = {r["mac"]: r for r in out["results"]}
    assert by_mac["aaa"]["ok"] is True
    assert by_mac["bbb"]["ok"] is False
    assert by_mac["bbb"]["error"] == "simulated udp timeout"


async def test_run_all_results_preserve_target_order() -> None:
    async def call(mac: str, ip: str) -> dict[str, Any]:
        # Make "aaa" finish last so completion order != input order.
        if mac == "aaa":
            await asyncio.sleep(0.02)
        return {"success": True}

    targets = [("aaa", "192.168.1.1"), ("bbb", "192.168.1.2"), ("ccc", "192.168.1.3")]
    result = await run_all(op="on", targets=targets, call=call, concurrency=16)
    assert [r.mac for r in result.results] == ["aaa", "bbb", "ccc"]


async def test_run_all_per_bulb_duration_is_recorded() -> None:
    async def call(mac: str, ip: str) -> dict[str, Any]:
        await asyncio.sleep(0.01)
        return {"success": True}

    result = await run_all(
        op="on", targets=[("aaa", "192.168.1.1")], call=call, concurrency=16
    )
    assert result.results[0].duration_ms >= 5


async def test_run_all_respects_concurrency_cap() -> None:
    """No more than `concurrency` bulb calls may be in flight simultaneously."""
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def call(mac: str, ip: str) -> dict[str, Any]:
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.02)
        finally:
            async with lock:
                in_flight -= 1
        return {"success": True}

    targets = [(f"mac{i:02d}", f"192.168.1.{i}") for i in range(20)]
    result = await run_all(op="on", targets=targets, call=call, concurrency=4)

    assert result.to_dict()["ok"] == 20
    assert peak <= 4, f"concurrency cap breached: peak={peak}, expected <= 4"
    assert peak >= 2, f"semaphore appears to serialise everything: peak={peak}"


from wiz_core.api import _concurrency_cap  # noqa: E402 - grouped with broadcast-cap tests


def test_concurrency_cap_default_is_min_of_n_and_16(monkeypatch: Any) -> None:
    monkeypatch.delenv("WIZ_ALL_CONCURRENCY", raising=False)
    assert _concurrency_cap(7) == 7  # fewer bulbs than the cap
    assert _concurrency_cap(16) == 16  # exactly the cap
    assert _concurrency_cap(50) == 16  # more bulbs than the cap -> clamped


def test_concurrency_cap_env_override(monkeypatch: Any) -> None:
    monkeypatch.setenv("WIZ_ALL_CONCURRENCY", "4")
    assert _concurrency_cap(50) == 4  # env lowers the ceiling
    assert _concurrency_cap(2) == 2  # still min(n, ceiling)


def test_concurrency_cap_env_invalid_falls_back_to_16(monkeypatch: Any) -> None:
    monkeypatch.setenv("WIZ_ALL_CONCURRENCY", "not-a-number")
    assert _concurrency_cap(50) == 16


def test_concurrency_cap_env_non_positive_falls_back_to_16(monkeypatch: Any) -> None:
    monkeypatch.setenv("WIZ_ALL_CONCURRENCY", "0")
    assert _concurrency_cap(50) == 16
    monkeypatch.setenv("WIZ_ALL_CONCURRENCY", "-3")
    assert _concurrency_cap(50) == 16


def test_concurrency_cap_floors_at_1_for_empty_registry(monkeypatch: Any) -> None:
    """asyncio.Semaphore requires value >= 1; n=0 must not produce 0."""
    monkeypatch.delenv("WIZ_ALL_CONCURRENCY", raising=False)
    assert _concurrency_cap(0) == 1


from pathlib import Path  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from wiz_core.api import create_app  # noqa: E402
from wiz_core.bulb import BulbError  # noqa: E402
from wiz_core.registry import Registry  # noqa: E402


class _StubClient:
    """In-memory bulb driver: records every set_pilot call, never fails."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get_pilot(self, ip: str) -> dict[str, Any]:
        return {"state": True}

    async def set_pilot(self, ip: str, **params: Any) -> dict[str, Any]:
        self.calls.append((ip, params))
        return {"success": True}


def _make_client(macs_ips: list[tuple[str, str]]) -> tuple[TestClient, _StubClient]:
    """Build a TestClient whose registry holds exactly `macs_ips`."""
    import tempfile

    reg = Registry(Path(tempfile.mkdtemp()) / "state.json")
    for mac, ip in macs_ips:
        reg.upsert_discovered({"mac": mac, "ip": ip, "rssi": -60})
    stub = _StubClient()

    async def fake_discover() -> int:
        return 0

    app = create_app(registry=reg, bulb=stub, run_discovery=fake_discover)
    return TestClient(app), stub


_THREE_BULBS = [
    ("d8a0118dc5c3", "192.168.1.3"),
    ("d8a011c0a795", "192.168.1.4"),
    ("d8a011c09c4f", "192.168.1.5"),
]


def test_all_on_flips_every_bulb() -> None:
    c, stub = _make_client(_THREE_BULBS)
    r = c.post("/bulb/all/on")
    assert r.status_code == 200
    body = r.json()
    assert body["op"] == "on"
    assert body["total"] == 3
    assert body["ok"] == 3
    assert body["failed"] == 0
    assert isinstance(body["duration_ms"], int)
    assert {res["mac"] for res in body["results"]} == {m for m, _ in _THREE_BULBS}
    assert all(res["ok"] is True for res in body["results"])
    # every bulb got setPilot state=True
    assert all(p == {"state": True} for _ip, p in stub.calls)


def test_all_off_flips_every_bulb() -> None:
    c, stub = _make_client(_THREE_BULBS)
    r = c.post("/bulb/all/off")
    assert r.status_code == 200
    assert r.json()["op"] == "off"
    assert r.json()["ok"] == 3
    assert all(p == {"state": False} for _ip, p in stub.calls)


def test_all_brightness_applies_level() -> None:
    c, stub = _make_client(_THREE_BULBS)
    r = c.post("/bulb/all/brightness", json={"level": 55})
    assert r.status_code == 200
    assert r.json()["op"] == "brightness"
    assert r.json()["ok"] == 3
    assert all(p == {"dimming": 55} for _ip, p in stub.calls)


def test_all_brightness_validates_body() -> None:
    c, _ = _make_client(_THREE_BULBS)
    r = c.post("/bulb/all/brightness", json={"level": 999})
    assert r.status_code == 422  # Pydantic rejects before the handler runs


def test_all_temp_applies_kelvin() -> None:
    c, stub = _make_client(_THREE_BULBS)
    r = c.post("/bulb/all/temp", json={"kelvin": 4000})
    assert r.status_code == 200
    assert r.json()["op"] == "temp"
    assert r.json()["ok"] == 3
    assert all(p == {"temp": 4000} for _ip, p in stub.calls)


def test_all_color_applies_rgb() -> None:
    c, stub = _make_client(_THREE_BULBS)
    r = c.post("/bulb/all/color", json={"r": 255, "g": 0, "b": 100})
    assert r.status_code == 200
    assert r.json()["op"] == "color"
    assert r.json()["ok"] == 3
    assert all(p == {"r": 255, "g": 0, "b": 100} for _ip, p in stub.calls)


def test_all_scene_by_name_applies_scene_id() -> None:
    c, stub = _make_client(_THREE_BULBS)
    r = c.post("/bulb/all/scene", json={"scene": "cozy"})
    assert r.status_code == 200
    assert r.json()["op"] == "scene"
    assert r.json()["ok"] == 3
    assert all(p == {"sceneId": 6} for _ip, p in stub.calls)  # cozy == id 6


def test_all_scene_with_speed() -> None:
    c, stub = _make_client(_THREE_BULBS)
    r = c.post("/bulb/all/scene", json={"scene": "party", "speed": 120})
    assert r.status_code == 200
    assert all(p == {"sceneId": 4, "speed": 120} for _ip, p in stub.calls)  # party == 4


def test_all_scene_unknown_name_is_400_before_any_bulb_touched() -> None:
    c, stub = _make_client(_THREE_BULBS)
    r = c.post("/bulb/all/scene", json={"scene": "nonsense"})
    assert r.status_code == 400
    assert stub.calls == []  # fail fast: no bulb was contacted


# silence "unused import" until later tasks use BulbError
_ = BulbError


class _OneBulbTimesOutClient(_StubClient):
    """set_pilot raises BulbError for one specific IP, succeeds for the rest."""

    def __init__(self, failing_ip: str) -> None:
        super().__init__()
        self.failing_ip = failing_ip

    async def set_pilot(self, ip: str, **params: Any) -> dict[str, Any]:
        if ip == self.failing_ip:
            raise BulbError("setPilot to 192.168.1.4: TimeoutError('udp timeout')")
        return await super().set_pilot(ip, **params)


def test_all_on_one_bulb_timeout_still_200_and_partial_ok() -> None:
    reg_path_holder: list[Any] = []

    import tempfile

    reg = Registry(Path(tempfile.mkdtemp()) / "state.json")
    for mac, ip in _THREE_BULBS:
        reg.upsert_discovered({"mac": mac, "ip": ip, "rssi": -60})
    reg_path_holder.append(reg)

    stub = _OneBulbTimesOutClient(failing_ip="192.168.1.4")  # the d8a011c0a795 bulb

    async def fake_discover() -> int:
        return 0

    app = create_app(registry=reg, bulb=stub, run_discovery=fake_discover)
    c = TestClient(app)

    r = c.post("/bulb/all/on")
    assert r.status_code == 200  # ALWAYS 200, even with a failure
    body = r.json()
    assert body["total"] == 3
    assert body["ok"] == 2
    assert body["failed"] == 1

    by_mac = {res["mac"]: res for res in body["results"]}
    assert by_mac["d8a0118dc5c3"]["ok"] is True
    assert by_mac["d8a011c09c4f"]["ok"] is True
    failed = by_mac["d8a011c0a795"]
    assert failed["ok"] is False
    assert "error" in failed
    assert "udp timeout" in failed["error"]
    assert "error" not in by_mac["d8a0118dc5c3"]  # success rows carry no error key


class _AllBulbsFailClient(_StubClient):
    """Every set_pilot call raises BulbError."""

    async def set_pilot(self, ip: str, **params: Any) -> dict[str, Any]:
        raise BulbError("simulated total LAN outage")


def test_all_on_every_bulb_fails_still_200() -> None:
    import tempfile

    reg = Registry(Path(tempfile.mkdtemp()) / "state.json")
    for mac, ip in _THREE_BULBS:
        reg.upsert_discovered({"mac": mac, "ip": ip, "rssi": -60})

    async def fake_discover() -> int:
        return 0

    app = create_app(registry=reg, bulb=_AllBulbsFailClient(), run_discovery=fake_discover)
    c = TestClient(app)

    r = c.post("/bulb/all/on")
    assert r.status_code == 200  # still 200 even when nothing worked
    body = r.json()
    assert body["op"] == "on"
    assert body["total"] == 3
    assert body["ok"] == 0
    assert body["failed"] == 3
    assert len(body["results"]) == 3
    for res in body["results"]:
        assert res["ok"] is False
        assert "error" in res
        assert "simulated total LAN outage" in res["error"]
