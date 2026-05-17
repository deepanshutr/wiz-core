"""32 built-in WiZ scenes, name ↔ id lookup."""

from __future__ import annotations

import pytest

from wiz_core.scenes import SCENES, resolve_scene


def test_all_32_scenes_present() -> None:
    assert len(SCENES) == 32
    assert min(SCENES) == 1 and max(SCENES) == 32


def test_resolve_by_id() -> None:
    assert resolve_scene(6) == 6  # cozy
    assert resolve_scene("6") == 6


def test_resolve_by_name_case_insensitive() -> None:
    assert resolve_scene("cozy") == 6
    assert resolve_scene("COZY") == 6
    assert resolve_scene(" cozy ") == 6
    assert resolve_scene("Warm White") == 11
    assert resolve_scene("night light") == 14


def test_unknown_scene_raises() -> None:
    with pytest.raises(ValueError, match="unknown scene"):
        resolve_scene("nope")
    with pytest.raises(ValueError, match="unknown scene"):
        resolve_scene(99)
