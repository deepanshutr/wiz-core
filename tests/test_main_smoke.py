"""Smoke test: app builds without hitting the LAN."""

from __future__ import annotations

from pathlib import Path

import pytest

from wiz_core.config import Settings
from wiz_core.main import build_app


def test_build_app_does_not_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WIZ_STATE_DIR", str(tmp_path))
    app = build_app(Settings(_env_file=None, state_dir=tmp_path))  # type: ignore[call-arg]
    # Just confirm the FastAPI app instance was returned; lifespan won't run here.
    assert app.title == "wiz-core"
