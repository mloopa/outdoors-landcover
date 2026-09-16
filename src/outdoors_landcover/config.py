"""Runtime configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def package_config_dir() -> Path:
    """Bundled config shipped with the package (fallback)."""
    here = Path(__file__).resolve().parent
    # Prefer repo-level config/ when running from a checkout.
    # outdoors/src/outdoors_landcover → parents[1] == outdoors/
    repo_config = here.parents[1] / "config"
    if repo_config.is_dir():
        return repo_config
    bundled = here / "config_data"
    if bundled.is_dir():
        return bundled
    return repo_config


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OUTDOORS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cache_dir: Path = Field(default=Path("./cache/osm"))
    temp_dir: Path = Field(default=Path("./tmp"))
    output_dir: Path = Field(default=Path("./outputs/landcover"))
    concurrency: int = Field(default=2, ge=1, le=32)
    provider: str = Field(default="geofabrik")
    base_url: str = Field(default="https://download.geofabrik.de")
    mappings_path: Path | None = None
    generalisation_path: Path | None = None
    countries_path: Path | None = None
    clms_path: Path | None = None
    clms_enabled: bool = Field(default=True)
    clms_year: int | None = None
    clms_local_root: Path | None = None
    cdse_username: str | None = None
    cdse_password: str | None = None
    log_level: str = Field(default="INFO")
    min_free_bytes: int = Field(default=1_000_000_000)  # 1 GiB soft check

    def resolved_mappings_path(self) -> Path:
        if self.mappings_path:
            return self.mappings_path
        return package_config_dir() / "mappings.yaml"

    def resolved_generalisation_path(self) -> Path:
        if self.generalisation_path:
            return self.generalisation_path
        return package_config_dir() / "generalisation.yaml"

    def resolved_countries_path(self) -> Path:
        if self.countries_path:
            return self.countries_path
        return package_config_dir() / "countries.yaml"

    def resolved_clms_path(self) -> Path:
        if self.clms_path:
            return self.clms_path
        return package_config_dir() / "clms.yaml"


def get_settings(**overrides: object) -> Settings:
    settings = Settings()
    return settings.model_copy(update={k: v for k, v in overrides.items() if v is not None})
