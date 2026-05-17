"""Env-driven configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PHILIPS_WIZ_BULB_",
        env_file=(".env", str(Path.home() / ".config" / "philips-wiz-bulb" / "state.env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bind: str = Field("127.0.0.1:8766", description="uvicorn bind address")
    broadcast: str = Field("192.168.1.255", description="UDP broadcast IP for getPilot")
    subnet: str = Field("192.168.1.0/24", description="Subnet to unicast-sweep on discover")
    refresh_interval_s: int = Field(60, description="Per-bulb refresh cadence")
    discover_interval_s: int = Field(600, description="Full re-discover cadence")
    log_level: str = Field("INFO", description="Python logging level")
    state_dir: Path = Field(
        default_factory=lambda: Path.home() / ".config" / "philips-wiz-bulb",
        description="Where state.json lives",
    )

    @property
    def state_path(self) -> Path:
        return self.state_dir / "state.json"


def load() -> Settings:
    return Settings()  # type: ignore[call-arg]
