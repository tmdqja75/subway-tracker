from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    tmap_app_key: str = ""
    subway_api_url: str = "http://localhost:8000"
    reitti_url: str = ""
    reitti_token: str = ""

    poll_interval_seconds: int = 5
    log_level: str = "INFO"
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "subway-tracker"
    otel_export_timeout_seconds: int = 5
    otel_metric_export_interval_millis: int = 10_000
    stations_csv: Path = BASE_DIR / "data" / "stations.csv"
    db_path: Path = BASE_DIR / "data" / "tracker.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
