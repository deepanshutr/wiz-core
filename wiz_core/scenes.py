"""The 32 built-in WiZ scenes."""

from __future__ import annotations

# id -> canonical lowercase name (per WiZ Open API + reverse-engineering notes).
SCENES: dict[int, str] = {
    1: "ocean",
    2: "romance",
    3: "sunset",
    4: "party",
    5: "fireplace",
    6: "cozy",
    7: "forest",
    8: "pastel colors",
    9: "wake up",
    10: "bedtime",
    11: "warm white",
    12: "daylight",
    13: "cool white",
    14: "night light",
    15: "focus",
    16: "relax",
    17: "true colors",
    18: "tv time",
    19: "plantgrowth",
    20: "spring",
    21: "summer",
    22: "fall",
    23: "deepdive",
    24: "jungle",
    25: "mojito",
    26: "club",
    27: "christmas",
    28: "halloween",
    29: "candlelight",
    30: "golden white",
    31: "pulse",
    32: "steampunk",
}

_NAME_TO_ID = {name: sid for sid, name in SCENES.items()}


def resolve_scene(scene: str | int) -> int:
    """Resolve a scene by id (int or numeric str) or canonical name."""
    if isinstance(scene, int):
        if scene in SCENES:
            return scene
        raise ValueError(f"unknown scene id: {scene}")
    s = scene.strip().lower()
    if s.isdigit():
        sid = int(s)
        if sid in SCENES:
            return sid
        raise ValueError(f"unknown scene id: {sid}")
    if s in _NAME_TO_ID:
        return _NAME_TO_ID[s]
    raise ValueError(f"unknown scene: {scene!r}")
