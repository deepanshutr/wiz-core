"""Protocol-agnostic best-effort fan-out engine for the `all` broadcast op.

Given a list of (mac, ip) targets and an injected per-bulb coroutine, runs
them concurrently under a semaphore, captures every result OR exception, and
assembles the pinned response envelope (see amendment Section A2). No
exception ever escapes `run_all`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

# A per-bulb call: takes (mac, ip), returns the driver's result dict, or raises.
BulbCall = Callable[[str, str], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class BulbCallResult:
    """Outcome of one bulb's op within a broadcast."""

    mac: str
    ok: bool
    duration_ms: int
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"mac": self.mac, "ok": self.ok, "duration_ms": self.duration_ms}
        if not self.ok:
            d["error"] = self.error
        return d


@dataclass(frozen=True)
class AllResult:
    """Aggregate outcome of a broadcast op across every targeted bulb."""

    op: str
    duration_ms: int
    results: list[BulbCallResult]

    def to_dict(self) -> dict[str, Any]:
        ok = sum(1 for r in self.results if r.ok)
        return {
            "op": self.op,
            "total": len(self.results),
            "ok": ok,
            "failed": len(self.results) - ok,
            "duration_ms": self.duration_ms,
            "results": [r.to_dict() for r in self.results],
        }


async def run_all(
    *,
    op: str,
    targets: list[tuple[str, str]],
    call: BulbCall,
    concurrency: int,
) -> AllResult:
    """Fan `call` out across every (mac, ip) in `targets`. Never raises.

    `concurrency` is the caller-computed cap (see api._concurrency_cap). For an
    empty `targets` list this returns an empty AllResult without scheduling
    anything.
    """
    started = time.monotonic()
    if not targets:
        return AllResult(op=op, duration_ms=_elapsed_ms(started), results=[])

    sem = asyncio.Semaphore(concurrency)

    async def _one(mac: str, ip: str) -> BulbCallResult:
        async with sem:
            return await _call_one(mac, ip, call)

    gathered = await asyncio.gather(
        *(_one(mac, ip) for mac, ip in targets),
        return_exceptions=True,
    )

    # return_exceptions=True is a belt-and-braces guard: _call_one already
    # catches everything, so a BaseException leaking here would be a bug in
    # _call_one. Convert any such leak into a failed result rather than
    # letting it escape run_all (the A2 contract: nothing ever bubbles out).
    results: list[BulbCallResult] = []
    for (mac, _ip), outcome in zip(targets, gathered, strict=True):
        if isinstance(outcome, BulbCallResult):
            results.append(outcome)
        else:
            results.append(
                BulbCallResult(mac=mac, ok=False, duration_ms=0, error=repr(outcome))
            )
    return AllResult(op=op, duration_ms=_elapsed_ms(started), results=results)


async def _call_one(mac: str, ip: str, call: BulbCall) -> BulbCallResult:
    """Run one bulb's op, catching every exception into a failed result."""
    started = time.monotonic()
    try:
        await call(mac, ip)
    except Exception as exc:  # intentional best-effort catch — per-bulb failures must not escape
        return BulbCallResult(
            mac=mac, ok=False, duration_ms=_elapsed_ms(started), error=_error_text(exc)
        )
    return BulbCallResult(mac=mac, ok=True, duration_ms=_elapsed_ms(started), error=None)


def _error_text(exc: Exception) -> str:
    """Human-readable per-bulb error string. Uses the exception message when
    present, falling back to the class name for argument-less exceptions."""
    msg = str(exc).strip()
    return msg if msg else type(exc).__name__


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
