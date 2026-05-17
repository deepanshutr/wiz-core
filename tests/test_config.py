"""Settings parse env-prefixed vars with sane defaults."""

from __future__ import annotations

import pytest

from wiz_core.config import Settings


def test_defaults_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear any env that would leak in
    for k in list(__import__("os").environ):
        if k.startswith("PHILIPS_WIZ_BULB_"):
            monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.bind == "127.0.0.1:8766"
    assert s.broadcast == "192.168.1.255"
    assert s.subnet == "192.168.1.0/24"
    assert s.refresh_interval_s == 60
    assert s.discover_interval_s == 600
    assert s.log_level == "INFO"
    assert str(s.state_dir).endswith("philips-wiz-bulb")


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHILIPS_WIZ_BULB_BIND", "127.0.0.1:9000")
    monkeypatch.setenv("PHILIPS_WIZ_BULB_BROADCAST", "10.0.0.255")
    monkeypatch.setenv("PHILIPS_WIZ_BULB_REFRESH_INTERVAL_S", "30")
    s = Settings(_env_file=None)
    assert s.bind == "127.0.0.1:9000"
    assert s.broadcast == "10.0.0.255"
    assert s.refresh_interval_s == 30
