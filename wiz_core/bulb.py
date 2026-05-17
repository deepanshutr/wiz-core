"""Async JSON-over-UDP client for Philips WiZ bulbs (port 38899)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

DEFAULT_PORT = 38899
DEFAULT_TIMEOUT = 1.5
DEFAULT_RETRIES = 2

log = logging.getLogger(__name__)


class BulbError(RuntimeError):
    """Raised when a WiZ call fails (timeout, network error, or WiZ error envelope)."""


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if not self.future.done():
            self.future.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if not self.future.done():
            self.future.set_exception(exc)


class BulbClient:
    """Stateless WiZ client. Safe for concurrent use; each call opens a fresh socket."""

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self.port = port
        self.timeout = timeout
        self.retries = retries

    async def get_pilot(self, ip: str) -> dict[str, Any]:
        return await self._call(ip, "getPilot", {})

    async def get_system_config(self, ip: str) -> dict[str, Any]:
        return await self._call(ip, "getSystemConfig", {})

    async def set_pilot(self, ip: str, **params: Any) -> dict[str, Any]:
        return await self._call(ip, "setPilot", params)

    async def _call(self, ip: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps({"method": method, "params": params}).encode()
        last_err: Exception | None = None
        backoff = self.timeout
        for attempt in range(self.retries + 1):
            try:
                resp = await self._send_once(ip, payload)
            except (TimeoutError, OSError) as exc:
                last_err = exc
                if attempt < self.retries:
                    log.debug("retrying %s after %r (attempt %d)", method, exc, attempt + 1)
                    await asyncio.sleep(backoff)
                    backoff *= 2
                continue
            if "error" in resp:
                # WiZ-side protocol error — deterministic, retrying is wasted round-trips.
                raise BulbError(
                    f"wiz error {resp['error'].get('code')}: {resp['error'].get('message')}"
                )
            return dict(resp.get("result", {}))
        log.warning("giving up on %s to %s after %d attempts", method, ip, self.retries + 1)
        raise BulbError(f"{method} to {ip}: {last_err!r}")

    async def _send_once(self, ip: str, payload: bytes) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            _Protocol, remote_addr=(ip, self.port)
        )
        try:
            transport.sendto(payload)
            try:
                data = await asyncio.wait_for(protocol.future, timeout=self.timeout)
            except TimeoutError as exc:
                raise TimeoutError(f"timeout waiting for {ip}:{self.port}") from exc
        finally:
            transport.close()
        return dict(json.loads(data.decode()))
