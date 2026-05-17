"""FastAPI HTTP surface."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .bulb import BulbError
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

    return app
