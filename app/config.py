from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    tmap_app_key: str = ""
    seoul_api_key: str = ""
    reitti_url: str = ""
    reitti_token: str = ""

    poll_interval_seconds: int = 15
    stations_csv: Path = BASE_DIR / "data" / "stations.csv"
    db_path: Path = BASE_DIR / "data" / "tracker.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
