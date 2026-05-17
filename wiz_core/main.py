"""FastAPI app entrypoint with lifespan-managed discovery loops."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import create_app
from .bulb import BulbClient
from .config import Settings
from .config import load as load_settings
from .discover import discover as discover_lan
from .registry import Registry

log = logging.getLogger(__name__)


def build_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    registry = Registry(cfg.state_path)
    bulb = BulbClient()

    async def run_discovery() -> int:
        before = len(registry.all())
        bulbs = await discover_lan(
            bulb, broadcast=cfg.broadcast, subnet=cfg.subnet
        )
        for b in bulbs:
            entry = registry.upsert_discovered(b)
            # Enrich with model info; best-effort.
            try:
                sys_cfg = await bulb.get_system_config(entry.last_ip)
                registry.enrich(entry.mac, sys_cfg)
            except Exception as e:
                log.debug("enrich %s failed: %r", entry.mac, e)
        registry.flush()
        return len(registry.all()) - before

    async def refresh_loop() -> None:
        while True:
            await asyncio.sleep(cfg.refresh_interval_s)
            for b in registry.all():
                try:
                    await bulb.get_pilot(b.last_ip)
                    registry.upsert_discovered(
                        {"mac": b.mac, "ip": b.last_ip, "rssi": None}
                    )
                except Exception:
                    log.debug("refresh %s missed", b.mac)
            registry.flush()

    async def rediscover_loop() -> None:
        while True:
            await asyncio.sleep(cfg.discover_interval_s)
            try:
                await run_discovery()
            except Exception as e:
                log.warning("background rediscover failed: %r", e)

    async def _boot_discover() -> None:
        try:
            await run_discovery()
        except Exception as e:
            log.warning("boot discovery failed: %r", e)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Fire boot discovery in the background so uvicorn binds the port
        # immediately; /bulbs returns [] until the LAN sweep populates the
        # registry (~30s on a /24). /health stays responsive throughout.
        t_boot = asyncio.create_task(_boot_discover())
        t1 = asyncio.create_task(refresh_loop())
        t2 = asyncio.create_task(rediscover_loop())
        try:
            yield
        finally:
            for t in (t_boot, t1, t2):
                t.cancel()
            await asyncio.gather(t_boot, t1, t2, return_exceptions=True)

    app = create_app(registry=registry, bulb=bulb, run_discovery=run_discovery)
    app.router.lifespan_context = lifespan
    return app


app = build_app()
