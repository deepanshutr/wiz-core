"""Tests for the ESP-TOUCH onboarding handler."""

from __future__ import annotations

from pathlib import Path

from esptouch import EsptouchError, NewBulb

from wiz_core.onboard import OnboardResult, onboard
from wiz_core.registry import Registry


def _registry(tmp_path: Path) -> Registry:
    return Registry(tmp_path / "state.json")


async def test_onboard_returns_joined_bulbs(tmp_path: Path, mocker) -> None:
    """A successful run yields status 'ok' and the joined-bulb payloads."""
    reg = _registry(tmp_path)

    async def fake_run(ssid, password, *, timeout_s, on_join, bssid, local_ip, **kw):
        # Simulate a bulb appearing: add it to the registry, let on_join see it.
        reg.upsert_discovered({"mac": "d8a0118dc5c3", "ip": "192.168.1.9", "rssi": -55})
        return await on_join()

    mocker.patch("wiz_core.onboard.esptouch_run", new=fake_run)

    async def fake_discovery() -> int:
        return 1

    result = await onboard(
        ssid="HomeNet", password="secret123", timeout_s=30,
        registry=reg, run_discovery=fake_discovery,
    )
    assert isinstance(result, OnboardResult)
    assert result.status == "ok"
    assert len(result.onboarded) == 1
    assert result.onboarded[0]["mac"] == "d8a0118dc5c3"
    assert result.onboarded[0]["ip"] == "192.168.1.9"
    assert "name" in result.onboarded[0]
    assert "rssi" in result.onboarded[0]


async def test_onboard_only_reports_newly_joined_macs(tmp_path: Path, mocker) -> None:
    """Bulbs already in the registry before onboarding are NOT reported."""
    reg = _registry(tmp_path)
    reg.upsert_discovered({"mac": "aaaaaaaaaaaa", "ip": "192.168.1.2", "rssi": -40})

    async def fake_run(ssid, password, *, timeout_s, on_join, bssid, local_ip, **kw):
        reg.upsert_discovered({"mac": "d8a0118dc5c3", "ip": "192.168.1.9", "rssi": -55})
        return await on_join()

    mocker.patch("wiz_core.onboard.esptouch_run", new=fake_run)

    async def fake_discovery() -> int:
        return 1

    result = await onboard(
        ssid="HomeNet", password="secret123", timeout_s=30,
        registry=reg, run_discovery=fake_discovery,
    )
    assert result.status == "ok"
    macs = {b["mac"] for b in result.onboarded}
    assert macs == {"d8a0118dc5c3"}  # the pre-existing aaaa... bulb excluded


async def test_onboard_timeout_when_no_bulb_joins(tmp_path: Path, mocker) -> None:
    """No new bulb -> status 'timeout' carrying the attempted duration."""
    reg = _registry(tmp_path)

    async def fake_run(ssid, password, *, timeout_s, on_join, bssid, local_ip, **kw):
        await on_join()  # discovery runs, finds nothing new
        return []

    mocker.patch("wiz_core.onboard.esptouch_run", new=fake_run)

    async def fake_discovery() -> int:
        return 0

    result = await onboard(
        ssid="HomeNet", password="secret123", timeout_s=45,
        registry=reg, run_discovery=fake_discovery,
    )
    assert result.status == "timeout"
    assert result.attempted_seconds == 45
    assert result.onboarded == []


async def test_onboard_internal_error_on_esptouch_failure(
    tmp_path: Path, mocker
) -> None:
    """An EsptouchError -> status 'error' with a detail string."""
    reg = _registry(tmp_path)

    async def boom(*a, **kw):
        raise EsptouchError("socket bind failed")

    mocker.patch("wiz_core.onboard.esptouch_run", new=boom)

    async def fake_discovery() -> int:
        return 0

    result = await onboard(
        ssid="HomeNet", password="secret123", timeout_s=30,
        registry=reg, run_discovery=fake_discovery,
    )
    assert result.status == "error"
    assert result.detail is not None
    assert "socket bind failed" in result.detail


async def test_onboard_passes_timeout_through_to_run(tmp_path: Path, mocker) -> None:
    """The handler forwards timeout_s into esptouch.run() unchanged."""
    reg = _registry(tmp_path)
    seen: dict[str, int] = {}

    async def fake_run(ssid, password, *, timeout_s, on_join, bssid, local_ip, **kw):
        seen["timeout_s"] = timeout_s
        return []

    mocker.patch("wiz_core.onboard.esptouch_run", new=fake_run)

    async def fake_discovery() -> int:
        return 0

    await onboard(
        ssid="HomeNet", password="secret123", timeout_s=120,
        registry=reg, run_discovery=fake_discovery,
    )
    assert seen["timeout_s"] == 120


def test_onboard_result_carries_newbulb_fields() -> None:
    """OnboardResult.from_bulbs maps NewBulb -> the §A1 payload dict."""
    bulbs = [NewBulb(mac="d8a0118dc5c3", ip="192.168.1.9", name="bulb-8", rssi=-55)]
    result = OnboardResult.from_bulbs(bulbs)
    assert result.status == "ok"
    assert result.onboarded == [
        {"mac": "d8a0118dc5c3", "ip": "192.168.1.9", "name": "bulb-8", "rssi": -55}
    ]
