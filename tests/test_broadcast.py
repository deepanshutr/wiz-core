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
