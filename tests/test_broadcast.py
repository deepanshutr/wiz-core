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
