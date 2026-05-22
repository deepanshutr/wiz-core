"""Protocol-agnostic best-effort fan-out engine for the `all` broadcast op.

Given a list of (mac, ip) targets and an injected per-bulb coroutine, runs
them concurrently under a semaphore, captures every result OR exception, and
assembles the pinned response envelope (see amendment Section A2). No
exception ever escapes `run_all`.
"""

from __future__ import annotations

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
    raise NotImplementedError  # fan-out arrives in Task 2


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
