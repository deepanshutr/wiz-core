"""FastAPI HTTP surface."""

from __future__ import annotations

import os
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .broadcast import run_all
from .bulb import BulbError
from .onboard import OnboardResult
from .onboard import onboard as run_onboard
from .registry import Bulb, Registry
from .scenes import SCENES, resolve_scene


class _BulbDriver(Protocol):
    async def get_pilot(self, ip: str) -> dict[str, Any]: ...
    async def set_pilot(self, ip: str, **params: Any) -> dict[str, Any]: ...


class BrightnessIn(BaseModel):
    level: Annotated[int, Field(ge=10, le=100)]


class TempIn(BaseModel):
    kelvin: Annotated[int, Field(ge=2200, le=6500)]


class ColorIn(BaseModel):
    r: Annotated[int, Field(ge=0, le=255)]
    g: Annotated[int, Field(ge=0, le=255)]
    b: Annotated[int, Field(ge=0, le=255)]


class SceneIn(BaseModel):
    scene: str | int
    speed: int | None = Field(None, ge=10, le=200)


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class DiscoverIn(BaseModel):
    passive: bool = False


class OnboardIn(BaseModel):
    ssid: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    timeout_s: int = Field(60, ge=10, le=300)
    # Accepted for cross-daemon body uniformity (amendment §A6.1) and
    # intentionally unused: ESP-TOUCH is an over-the-air broadcast with
    # no setup AP to select. Only yeelight-core consumes setup_ssid.
    # No `extra="forbid"` — the model must tolerate this field so a
    # multiplexer can POST one uniform body to every -core daemon.
    setup_ssid: str | None = Field(default=None, max_length=64)


def _bulb_payload(b: Bulb) -> dict[str, Any]:
    return {
        "protocol": "wiz",
        "mac": b.mac,
        "name": b.name,
        "ip": b.last_ip,
        "rssi": b.last_rssi,
        "module": b.module,
        "fw_version": b.fw_version,
        "cct_range": list(b.cct_range) if b.cct_range else None,
        "discovered_at": b.discovered_at,
        "last_seen": b.last_seen,
    }


# Hard ceiling on concurrent per-bulb UDP calls during a broadcast. Protects
# the LAN / router from a many-bulb burst. Overridable via WIZ_ALL_CONCURRENCY
# (a positive integer). Pinned by amendment Section A2.
_ALL_CONCURRENCY_DEFAULT = 16


def _concurrency_cap(n_bulbs: int) -> int:
    """Concurrency bound for a broadcast over `n_bulbs` bulbs.

    Returns ``min(n_bulbs, ceiling)`` where ``ceiling`` is 16 by default or the
    value of the ``WIZ_ALL_CONCURRENCY`` env var when that is a positive
    integer. Always returns at least 1, because ``asyncio.Semaphore`` rejects
    a value below 1 (an empty registry would otherwise yield 0).
    """
    ceiling = _ALL_CONCURRENCY_DEFAULT
    raw = os.environ.get("WIZ_ALL_CONCURRENCY")
    if raw is not None:
        try:
            parsed = int(raw)
        except ValueError:
            parsed = 0
        if parsed > 0:
            ceiling = parsed
    return max(1, min(n_bulbs, ceiling))


def create_app(
    *,
    registry: Registry,
    bulb: _BulbDriver,
    run_discovery: Callable[[], Coroutine[Any, Any, int]],
) -> FastAPI:
    app = FastAPI(title="wiz-core")

    def resolve_or_404(target: str) -> Bulb:
        b = registry.resolve(target)
        if b is None:
            raise HTTPException(status_code=404, detail=f"no bulb matches {target!r}")
        return b

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/bulbs")
    async def list_bulbs() -> dict[str, Any]:
        return {"bulbs": [_bulb_payload(b) for b in registry.all()]}

    @app.get("/bulbs/default")
    async def default_bulb() -> dict[str, Any]:
        b = registry.default()
        if b is None:
            raise HTTPException(409, "no bulbs known; POST /discover first")
        try:
            pilot = await bulb.get_pilot(b.last_ip)
        except BulbError as e:
            raise HTTPException(504, str(e)) from e
        return {**_bulb_payload(b), **pilot}

    @app.post("/discover")
    async def discover(body: DiscoverIn) -> dict[str, Any]:
        n = await run_discovery()
        registry.flush()
        return {"discovered": n, "total": len(registry.all())}

    @app.get("/bulb/{target}")
    async def get_bulb(target: str) -> dict[str, Any]:
        b = resolve_or_404(target)
        try:
            pilot = await bulb.get_pilot(b.last_ip)
        except BulbError as e:
            raise HTTPException(504, str(e)) from e
        return {**_bulb_payload(b), **pilot}

    async def _set(target_bulb: Bulb, /, **params: Any) -> dict[str, Any]:
        try:
            return await bulb.set_pilot(target_bulb.last_ip, **params)
        except BulbError as e:
            raise HTTPException(504, str(e)) from e

    async def _broadcast(op: str, params: dict[str, Any]) -> dict[str, Any]:
        """Apply one setPilot param dict to every registered bulb, best-effort.

        Always returns the A2 envelope (HTTP 200). Per-bulb failures are caught
        by run_all and surfaced in each bulb's `error` field; no exception ever
        escapes this coroutine.
        """
        bulbs = registry.all()
        targets = [(b.mac, b.last_ip) for b in bulbs]

        async def call(_mac: str, ip: str) -> dict[str, Any]:
            return await bulb.set_pilot(ip, **params)

        result = await run_all(
            op=op,
            targets=targets,
            call=call,
            concurrency=_concurrency_cap(len(targets)),
        )
        return result.to_dict()

    # Broadcast routes MUST be registered before the parametrized /bulb/{target}/...
    # routes. Starlette 1.x matches in registration order; a static path segment
    # ("all") does not outrank a path parameter by position alone.
    @app.post("/bulb/all/on")
    async def all_on() -> dict[str, Any]:
        return await _broadcast("on", {"state": True})

    @app.post("/bulb/all/off")
    async def all_off() -> dict[str, Any]:
        return await _broadcast("off", {"state": False})

    @app.post("/bulb/all/brightness")
    async def all_brightness(body: BrightnessIn) -> dict[str, Any]:
        return await _broadcast("brightness", {"dimming": int(body.level)})

    @app.post("/bulb/all/temp")
    async def all_temp(body: TempIn) -> dict[str, Any]:
        return await _broadcast("temp", {"temp": int(body.kelvin)})

    @app.post("/bulb/all/color")
    async def all_color(body: ColorIn) -> dict[str, Any]:
        return await _broadcast("color", {"r": int(body.r), "g": int(body.g), "b": int(body.b)})

    @app.post("/bulb/all/scene")
    async def all_scene(body: SceneIn) -> dict[str, Any]:
        # An unknown scene id/name is wrong for EVERY bulb, so fail the whole
        # request fast with 400 (mirrors the single-bulb /scene handler) rather
        # than reporting it as a per-bulb error against all N bulbs.
        try:
            sid = resolve_scene(body.scene)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        params: dict[str, Any] = {"sceneId": sid}
        if body.speed is not None:
            params["speed"] = body.speed
        return await _broadcast("scene", params)

    @app.post("/bulb/{target}/on")
    async def on(target: str) -> dict[str, Any]:
        return await _set(resolve_or_404(target), state=True)

    @app.post("/bulb/{target}/off")
    async def off(target: str) -> dict[str, Any]:
        return await _set(resolve_or_404(target), state=False)

    @app.post("/bulb/{target}/brightness")
    async def brightness(target: str, body: BrightnessIn) -> dict[str, Any]:
        return await _set(resolve_or_404(target), dimming=int(body.level))

    @app.post("/bulb/{target}/temp")
    async def temp(target: str, body: TempIn) -> dict[str, Any]:
        return await _set(resolve_or_404(target), temp=int(body.kelvin))

    @app.post("/bulb/{target}/color")
    async def color(target: str, body: ColorIn) -> dict[str, Any]:
        return await _set(resolve_or_404(target), r=int(body.r), g=int(body.g), b=int(body.b))

    @app.post("/bulb/{target}/scene")
    async def scene(target: str, body: SceneIn) -> dict[str, Any]:
        try:
            sid = resolve_scene(body.scene)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        params: dict[str, Any] = {"sceneId": sid}
        if body.speed is not None:
            params["speed"] = body.speed
        return await _set(resolve_or_404(target), **params)

    @app.post("/bulb/{target}/name")
    async def name(target: str, body: NameIn) -> dict[str, Any]:
        b = resolve_or_404(target)
        registry.rename(b.mac, body.name)
        registry.flush()
        return _bulb_payload(b)

    @app.get("/scenes")
    async def scenes() -> dict[str, Any]:
        return {"scenes": [{"id": sid, "name": nm} for sid, nm in sorted(SCENES.items())]}

    @app.post("/onboard")
    async def onboard_route(body: OnboardIn) -> dict[str, Any]:
        # ESP-TOUCH (Espressif SmartConfig): broadcast length-encoded
        # Wi-Fi credentials to a setup-mode WiZ bulb, poll discovery for
        # the new MAC. Contract is pinned by amendment §A1. The protocol
        # itself lives in the standalone `esptouch` library (a dependency).
        result: OnboardResult = await run_onboard(
            ssid=body.ssid,
            password=body.password,
            timeout_s=body.timeout_s,
            registry=registry,
            run_discovery=run_discovery,
        )
        if result.status == "ok":
            return {"onboarded": result.onboarded}
        if result.status == "timeout":
            raise HTTPException(
                status_code=408,
                detail={
                    "error": "timeout",
                    "attempted_seconds": result.attempted_seconds,
                },
            )
        # result.status == "error"
        raise HTTPException(
            status_code=500,
            detail={"error": "esptouch_internal", "detail": result.detail},
        )

    return app
